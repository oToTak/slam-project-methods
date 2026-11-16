// orbslam3_tum.cc - headless ORB-SLAM3 driver for TUM-style image folders (rgb.txt + images), mono and mono-inertial.
//
// Written against ORB-SLAM3 commit 4452a3c4ab75b1cde34e5505a36ec3f9edcdc4c4 (API read from its sources: System.h, System.cc,
// Tracking.h, ImuTypes.h, Examples/Monocular-Inertial/mono_inertial_euroc.cc). NOT COMPILED YET (prepared offline, see
// scripts/orbslam3/PREP_NOTES.md): the first build may need small fixes.
//
// Why our own driver instead of patching the upstream examples:
//  * the upstream examples read EuRoC/TUM list formats and sleep between frames to imitate real time;
//  * we need the viewer OFF (headless WSL), a per-frame trajectory (SaveTrajectoryEuRoC, NOT keyframes only), IMU rows in
//    our own simple text format, and exit codes that the run script can trust.
//
// Usage:
//   orbslam3_tum VOCAB SETTINGS SEQ_DIR RAW_TRAJ_OUT [--imu FILE] [--kf-out FILE] [--max-frames N] [--pace F]
//     SEQ_DIR/rgb.txt   '#timestamp path' per frame (path relative to SEQ_DIR, or absolute), seconds
//     --imu FILE        '#timestamp ax ay az gx gy gz' (scripts/orbslam3/convert_imu.py output): selects IMU_MONOCULAR
//     --max-frames N    only the first N frames (smoke tests)
//     --pace F          F > 0: sleep so that a frame takes at least dt/F wall time (F = 1: real time like the upstream
//                       examples). Default 0 = no sleeping at all (the task asks for it; whether it changes the tracking
//                       result because local mapping runs in its own thread is TODO(verify), see PREP_NOTES.md)
// Times given to ORB-SLAM3 are RELATIVE to the first frame (double precision of 1.7e9 s epoch times is only 2e-7 s);
// scripts/orbslam3/postprocess.py adds the origin back. RAW_TRAJ_OUT therefore holds 1e9*(t - t_first_frame) in column 1.
//
// Exit codes: 0 trajectory saved | 2 usage | 3 data error (missing/unreadable file, bad timestamps) |
//             4 tracking never reached the OK state: nothing saved (System::SaveTrajectoryEuRoC would crash on an empty atlas)
// stdout contains 'ORB3_STATS ...' (state histogram, timing) as the last-but-few lines for the run log.

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <opencv2/core/core.hpp>
#include <opencv2/imgcodecs/imgcodecs.hpp>

#include "System.h"
#include "ImuTypes.h"

namespace {

struct FrameEntry {
    long double t;      // absolute seconds (long double: ~1e-10 s resolution at epoch 1.7e9)
    std::string path;
};

struct ImuEntry {
    long double t;
    float a[3];
    float w[3];
};

bool load_frames(const std::string &seq_dir, std::vector<FrameEntry> &out)
{
    std::ifstream f((seq_dir + "/rgb.txt").c_str());
    if (!f)
        return false;
    std::string line;
    while (std::getline(f, line)) {
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        if (line.empty() || line[0] == '#')
            continue;
        std::istringstream ss(line);
        std::string ts, rel;
        if (!(ss >> ts >> rel))
            continue;
        FrameEntry e;
        e.t = std::strtold(ts.c_str(), nullptr);
        e.path = (!rel.empty() && rel[0] == '/') ? rel : seq_dir + "/" + rel;
        out.push_back(e);
    }
    return !out.empty();
}

bool load_imu(const std::string &path, std::vector<ImuEntry> &out)
{
    std::ifstream f(path.c_str());
    if (!f)
        return false;
    std::string line;
    while (std::getline(f, line)) {
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        if (line.empty() || line[0] == '#')
            continue;
        std::istringstream ss(line);
        std::string ts;
        double v[6];
        if (!(ss >> ts >> v[0] >> v[1] >> v[2] >> v[3] >> v[4] >> v[5]))
            continue;
        ImuEntry e;
        e.t = std::strtold(ts.c_str(), nullptr);
        for (int i = 0; i < 3; ++i) {
            e.a[i] = static_cast<float>(v[i]);        // accelerometer m/s^2 (specific force)
            e.w[i] = static_cast<float>(v[3 + i]);    // gyroscope rad/s
        }
        out.push_back(e);
    }
    return !out.empty();
}

int usage()
{
    std::cerr << "usage: orbslam3_tum VOCAB SETTINGS SEQ_DIR RAW_TRAJ_OUT [--imu FILE] [--kf-out FILE] [--max-frames N] [--pace F]\n";
    return 2;
}

}  // namespace

int main(int argc, char **argv)
{
    if (argc < 5)
        return usage();
    const std::string vocab = argv[1], settings = argv[2], seq_dir = argv[3], raw_out = argv[4];
    std::string imu_path, kf_out;
    long max_frames = 0;
    double pace = 0.0;
    for (int i = 5; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--imu" && i + 1 < argc)
            imu_path = argv[++i];
        else if (a == "--kf-out" && i + 1 < argc)
            kf_out = argv[++i];
        else if (a == "--max-frames" && i + 1 < argc)
            max_frames = std::atol(argv[++i]);
        else if (a == "--pace" && i + 1 < argc)
            pace = std::atof(argv[++i]);
        else
            return usage();
    }
    const bool use_imu = !imu_path.empty();

    std::vector<FrameEntry> frames;
    if (!load_frames(seq_dir, frames)) {
        std::cerr << "ERROR: cannot read " << seq_dir << "/rgb.txt (or it has no frames)\n";
        return 3;
    }
    if (max_frames > 0 && static_cast<size_t>(max_frames) < frames.size())
        frames.resize(static_cast<size_t>(max_frames));
    for (size_t i = 1; i < frames.size(); ++i) {
        if (!(frames[i].t > frames[i - 1].t)) {
            std::cerr << "ERROR: rgb.txt timestamps are not strictly increasing at line " << i + 1 << "\n";
            return 3;
        }
    }
    std::vector<ImuEntry> imu;
    if (use_imu && !load_imu(imu_path, imu)) {
        std::cerr << "ERROR: cannot read IMU file " << imu_path << " (or it has no rows)\n";
        return 3;
    }
    const long double t0 = frames.front().t;

    // Same bookkeeping as the upstream mono_inertial_euroc.cc: the first frame gets no IMU, every later frame gets all
    // samples with t <= frame time that were not used yet, starting one sample BEFORE the first frame.
    size_t first_imu = 0;
    if (use_imu) {
        while (first_imu < imu.size() && imu[first_imu].t <= t0)
            ++first_imu;
        if (first_imu > 0)
            --first_imu;
    }

    std::cout.precision(17);
    std::cout << "ORB3_INFO frames=" << frames.size() << " imu_rows=" << imu.size()
              << " mode=" << (use_imu ? "mono_inertial" : "mono") << " pace=" << pace << std::endl;

    // bUseViewer = false: no Pangolin window, works without a display.
    ORB_SLAM3::System SLAM(vocab, settings,
                           use_imu ? ORB_SLAM3::System::IMU_MONOCULAR : ORB_SLAM3::System::MONOCULAR, false);

    std::map<int, size_t> state_hist;
    bool ever_ok = false;
    double track_s = 0.0;
    const auto wall0 = std::chrono::steady_clock::now();
    std::vector<ORB_SLAM3::IMU::Point> imu_meas;

    for (size_t ni = 0; ni < frames.size(); ++ni) {
        cv::Mat im = cv::imread(frames[ni].path, cv::IMREAD_UNCHANGED);
        if (im.empty()) {
            std::cerr << "ERROR: cannot read image " << frames[ni].path << "\n";
            return 3;
        }
        const double tframe = static_cast<double>(frames[ni].t - t0);

        imu_meas.clear();
        if (use_imu && ni > 0) {
            while (first_imu < imu.size() && imu[first_imu].t <= frames[ni].t) {
                const ImuEntry &m = imu[first_imu];
                imu_meas.push_back(ORB_SLAM3::IMU::Point(m.a[0], m.a[1], m.a[2], m.w[0], m.w[1], m.w[2],
                                                         static_cast<double>(m.t - t0)));
                ++first_imu;
            }
        }

        const auto t1 = std::chrono::steady_clock::now();
        SLAM.TrackMonocular(im, tframe, imu_meas);   // TrackMonocular ignores imu_meas for the pure mono sensor
        const auto t2 = std::chrono::steady_clock::now();
        const double dt_track = std::chrono::duration_cast<std::chrono::duration<double>>(t2 - t1).count();
        track_s += dt_track;

        const int st = SLAM.GetTrackingState();
        ++state_hist[st];
        if (st == ORB_SLAM3::Tracking::OK)
            ever_ok = true;

        if (pace > 0.0 && ni + 1 < frames.size()) {
            const double period = static_cast<double>(frames[ni + 1].t - frames[ni].t) / pace;
            if (dt_track < period)
                std::this_thread::sleep_for(std::chrono::duration<double>(period - dt_track));
        }
        if ((ni + 1) % 500 == 0)
            std::cout << "ORB3_PROGRESS " << ni + 1 << "/" << frames.size() << " state=" << st << std::endl;
    }

    const double wall_s =
        std::chrono::duration_cast<std::chrono::duration<double>>(std::chrono::steady_clock::now() - wall0).count();
    std::ostringstream hist;
    for (std::map<int, size_t>::const_iterator it = state_hist.begin(); it != state_hist.end(); ++it)
        hist << (it == state_hist.begin() ? "" : ",") << it->first << ":" << it->second;
    // states (Tracking.h): -1 SYSTEM_NOT_READY 0 NO_IMAGES_YET 1 NOT_INITIALIZED 2 OK 3 RECENTLY_LOST 4 LOST 5 OK_KLT
    std::cout << "ORB3_STATS frames=" << frames.size() << " ever_ok=" << (ever_ok ? 1 : 0) << " state_hist=" << hist.str()
              << " track_s=" << track_s << " wall_s=" << wall_s << std::endl;

    SLAM.Shutdown();
    if (!ever_ok) {
        std::cerr << "ERROR: tracking never reached the OK state: no trajectory saved\n";
        std::cout.flush();
        std::_Exit(4);
    }
    // Per-frame trajectory (all frames with a pose, relative to the first keyframe of the biggest map), NOT keyframes only.
    SLAM.SaveTrajectoryEuRoC(raw_out);
    if (!kf_out.empty())
        SLAM.SaveKeyFrameTrajectoryEuRoC(kf_out);   // diagnostics only
    std::cout << "ORB3_DONE saved " << raw_out << std::endl;
    std::cout.flush();
    std::fflush(nullptr);
    // Skip the destructors: ORB-SLAM3 threads may still be finishing after Shutdown() (it does not join them), and a crash
    // at exit must not turn a finished run into a failed one.
    std::_Exit(0);
}
