// dso_headless: headless, un-throttled, per-frame-trajectory driver for DSO (JakobEngel/dso @7b0c99f), mono.
//
// Why our own main instead of upstream's dso_dataset (src/main_dso_pangolin.cpp):
//   * dso_dataset is only built when Pangolin is found (CMakeLists.txt: "if (OpenCV_FOUND AND Pangolin_FOUND)") and always links it,
//     even with nogui=1. This driver needs only libdso.a (built WITHOUT Pangolin), so there is no Pangolin and no GL/Wayland apt set.
//   * upstream's result.txt is NOT per frame: setting_onlyLogKFPoses defaults to true (src/util/settings.cpp:192) and
//     FullSystem::printResult then skips every frame with marginalizedAt == id, i.e. all non-keyframes AND the keyframes still
//     active at the end. This driver writes every frame that has a valid pose, keyed by the frame INDEX (not by a timestamp),
//     so the post-processing maps poses to rgb.txt rows exactly.
// What is copied from main_dso_pangolin.cpp (same semantics): preset 0 settings, mode=1 settings (no photometric calibration),
// linearizeOperation = (playbackSpeed == 0) i.e. sequential tracking/mapping with NO real-time throttling and NO sleeping,
// the reset-on-failed-initialisation rule (only during the first 250 frames) and the stop on isLost.
//
// Usage: dso_headless <camera.txt> <images.txt> <out_dir> [--max-frames N] [--nomt] [--quiet]
//   camera.txt  DSO camera file (RadTan ... / 640 480 / crop / 640 480)
//   images.txt  one image path per line, in frame order (absolute paths; the line number - 1 is the frame index)
//   out_dir     receives poses_refined.txt, poses_tracked.txt, poses_keyframes.txt   ('<frame_index> x y z qx qy qz qw', camToWorld)
// Exit codes: 0 finished (also when DSO got lost half-way: see DSO_STATS), 2 usage, 3 bad calibration / unreadable image,
//             4 no frame got a valid pose (initialisation never succeeded).
// Compiled with -fno-access-control (see CMakeLists.txt) only to read FullSystem::allFrameHistory / allKeyFramesHistory.
// NOT COMPILED YET: written offline against the sources of the pinned commit.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>

#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <set>
#include <string>
#include <vector>

#include "IOWrapper/Output3DWrapper.h"
#include "IOWrapper/ImageDisplay.h"
#include "IOWrapper/ImageRW.h"

#include <boost/thread.hpp>
#include "util/settings.h"
#include "util/globalFuncs.h"
#include "util/globalCalib.h"
#include "util/NumType.h"
#include "util/Undistort.h"
#include "util/ImageAndExposure.h"
#include "util/MinimalImage.h"
#include "FullSystem/FullSystem.h"
#include "OptimizationBackend/MatrixAccumulators.h"
#include "FullSystem/PixelSelector2.h"

using namespace dso;

static double now_s()
{
    struct timeval tv;
    gettimeofday(&tv, NULL);
    return tv.tv_sec + tv.tv_usec * 1e-6;
}

static std::vector<std::string> read_list(const std::string& path)
{
    std::vector<std::string> v;
    std::ifstream f(path.c_str());
    std::string line;
    while (std::getline(f, line))
    {
        while (!line.empty() && (line[line.size() - 1] == '\r' || line[line.size() - 1] == ' ')) line.erase(line.size() - 1);
        if (!line.empty()) v.push_back(line);
    }
    return v;
}

static bool pose_finite(const SE3& T)
{
    Eigen::Quaterniond q = T.so3().unit_quaternion();
    for (int i = 0; i < 3; i++) if (!std::isfinite((double)T.translation()[i])) return false;
    return std::isfinite(q.x()) && std::isfinite(q.y()) && std::isfinite(q.z()) && std::isfinite(q.w());
}

static void write_pose(std::ostream& o, int idx, const SE3& T)
{
    Eigen::Quaterniond q = T.so3().unit_quaternion();
    o << idx << " " << T.translation().transpose() << " " << q.x() << " " << q.y() << " " << q.z() << " " << q.w() << "\n";
}

int main(int argc, char** argv)
{
    if (argc < 4)
    {
        fprintf(stderr, "usage: %s <camera.txt> <images.txt> <out_dir> [--max-frames N] [--nomt] [--quiet]\n", argv[0]);
        return 2;
    }
    std::string calib = argv[1], listFile = argv[2], outDir = argv[3];
    int maxFrames = 0;
    bool nomt = false, quiet = false;
    for (int i = 4; i < argc; i++)
    {
        if (!strcmp(argv[i], "--max-frames") && i + 1 < argc) maxFrames = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--nomt")) nomt = true;
        else if (!strcmp(argv[i], "--quiet")) quiet = true;
        else { fprintf(stderr, "unknown argument '%s'\n", argv[i]); return 2; }
    }

    // ---- settings: preset 0 + mode 1 + headless (main_dso_pangolin.cpp, settingsDefault(0) and "mode=1") -----------------
    setting_desiredImmatureDensity = 1500;
    setting_desiredPointDensity = 2000;
    setting_minFrames = 5;
    setting_maxFrames = 7;
    setting_maxOptIterations = 6;
    setting_minOptIterations = 1;
    setting_logStuff = false;                 // nolog=1 (no logs/ directory needed)
    setting_photometricCalibration = 0;       // mode=1: NO photometric calibration
    setting_affineOptModeA = 0;
    setting_affineOptModeB = 0;
    disableAllDisplay = true;                 // nogui=1 (also makes IOWrap::displayImage / waitKey return immediately)
    setting_onlyLogKFPoses = false;           // we write our own per-frame files; this keeps printResult() meaningful too
    if (quiet) setting_debugout_runquiet = true;
    if (nomt) multiThreading = false;
    // playbackSpeed == 0 in the original means: no sleeping at all and fullSystem->linearizeOperation = true (see below)

    std::vector<std::string> files = read_list(listFile);
    if (files.empty()) { fprintf(stderr, "no images in %s\n", listFile.c_str()); return 2; }
    if (maxFrames > 0 && (int)files.size() > maxFrames) files.resize(maxFrames);

    Undistort* undistort = Undistort::getUndistorterForFile(calib, "", "");   // no gamma, no vignette file
    if (undistort == 0 || !undistort->isValid())
    {
        fprintf(stderr, "cannot read a valid camera calibration from %s\n", calib.c_str());
        return 3;
    }
    {
        Eigen::Matrix3f K = undistort->getK().cast<float>();
        setGlobalCalib(undistort->getSize()[0], undistort->getSize()[1], K);
    }
    float* gamma = (undistort->photometricUndist != 0) ? undistort->photometricUndist->getG() : 0;   // 0 without a file

    FullSystem* fs = new FullSystem();
    fs->setGammaFunction(gamma);
    fs->linearizeOperation = true;

    const int n = (int)files.size();
    int fed = 0, resets = 0, lostAt = -1;
    double t0 = now_s();
    for (int ii = 0; ii < n; ii++)
    {
        MinimalImageB* raw = IOWrap::readImageBW_8U(files[ii]);
        if (raw == 0)
        {
            fprintf(stderr, "cannot read image %d: %s\n", ii, files[ii].c_str());
            return 3;
        }
        // exposure 1.0 and timestamp = frame index: neither is used by DSO without photometric calibration and without
        // setting_keyframesPerSecond; the frame index is what we key the output on.
        ImageAndExposure* img = undistort->undistort<unsigned char>(raw, 1.0f, (double)ii);
        delete raw;

        fs->addActiveFrame(img, ii);
        fed++;
        delete img;

        if (fs->initFailed || setting_fullResetRequested)
        {
            if (ii < 250 || setting_fullResetRequested)
            {
                printf("RESETTING!\n");
                delete fs;
                resets++;
                fs = new FullSystem();
                fs->setGammaFunction(gamma);
                fs->linearizeOperation = true;
                setting_fullResetRequested = false;
            }
        }
        if (fs->isLost)
        {
            printf("LOST!!\n");
            lostAt = ii;
            break;
        }
    }
    fs->blockUntilMappingIsFinished();
    double wall = now_s() - t0;

    // ---- per-frame output ---------------------------------------------------------------------------------------------------
    std::set<FrameShell*> kf(fs->allKeyFramesHistory.begin(), fs->allKeyFramesHistory.end());
    std::ofstream refined((outDir + "/poses_refined.txt").c_str());
    std::ofstream tracked((outDir + "/poses_tracked.txt").c_str());
    std::ofstream kfout((outDir + "/poses_keyframes.txt").c_str());
    if (!refined.good() || !tracked.good() || !kfout.good())
    {
        fprintf(stderr, "cannot write into %s (does the directory exist?)\n", outDir.c_str());
        return 3;
    }
    refined << std::setprecision(15);
    tracked << std::setprecision(15);
    kfout << std::setprecision(15);
    int valid = 0, nkf = 0, dropped = 0;
    for (size_t k = 0; k < fs->allFrameHistory.size(); k++)
    {
        FrameShell* s = fs->allFrameHistory[k];
        if (!s->poseValid) continue;               // frames seen while the initializer was still running
        // the frame on which tracking got LOST keeps a pose DSO never validated: not a result
        if (lostAt >= 0 && s->incoming_id == lostAt) { dropped++; continue; }
        if (!pose_finite(s->camToWorld)) { dropped++; continue; }
        valid++;
        // poses_tracked: camToWorld as stored (keyframes: final optimised pose; other frames: the pose at tracking time)
        write_pose(tracked, s->incoming_id, s->camToWorld);
        // poses_refined: non-keyframes re-expressed against the FINAL pose of the keyframe they were tracked against
        // (the same idea as ORB-SLAM3's SaveTrajectory: relative pose to the reference keyframe, final keyframe pose).
        bool isKf = kf.count(s) > 0;
        if (isKf) { nkf++; write_pose(kfout, s->incoming_id, s->camToWorld); }
        if (!isKf && s->trackingRef != 0) write_pose(refined, s->incoming_id, s->trackingRef->camToWorld * s->camToTrackingRef);
        else write_pose(refined, s->incoming_id, s->camToWorld);
    }
    refined.close();
    tracked.close();
    kfout.close();
    fs->printResult((outDir + "/result_stock_format.txt").c_str());   // same call as upstream, for comparison only

    printf("DSO_STATS frames=%d fed=%d valid=%d keyframes=%d dropped=%d resets=%d lost=%d lost_at=%d initialized=%d wall_s=%.1f\n",
           n, fed, valid, nkf, dropped, resets, lostAt >= 0 ? 1 : 0, lostAt, fs->initialized ? 1 : 0, wall);
    int rc = (valid > 0) ? 0 : 4;
    delete fs;
    delete undistort;
    return rc;
}
