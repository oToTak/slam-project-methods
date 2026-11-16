// ov_ucu_driver: ROS-free, headless, un-throttled driver for OpenVINS (rpng/open_vins) mono + IMU on a TUM-style folder.
//
// OpenVINS built with -DENABLE_ROS=OFF has no dataset runner of its own (only run_simulation); the docs say "You must construct the
// VioManagerOptions struct yourself and feed inertial and image data into the correct functions" (https://docs.openvins.com/gs-installing-free.html).
// This file does exactly that, following src/run_simulation.cpp (config loading, single-threaded settings for repeatability) and
// src/ros/ROS2Visualizer.cpp (callback_inertial: a camera frame is processed only after an IMU sample newer than the frame time
// (minus the camera-IMU offset) was fed; callback_monocular: MONO8 image, zero mask).
//
// Usage: ov_ucu_driver <estimator_config.yaml> <rgb.txt> <imu.txt> <out_poses.txt> [--max-frames N]
//   rgb.txt   TUM rgb.txt ('timestamp relative/path.png'); paths are relative to the folder of rgb.txt (or absolute)
//   imu.txt   'timestamp ax ay az gx gy gz' in seconds / m/s^2 / rad/s (the cleaned file of scripts/orbslam3/convert_imu.py)
//   out_poses.txt  one line per camera frame that got a pose after its filter update:
//     <frame_index> px py pz qx qy qz qw   cqx cqy cqz cqw cpx cpy cpz
//       p, q   = pose of the IMU in the global frame: p_IinG and the HAMILTON quaternion of R_ItoG (= R_GtoI^T, R_GtoI = quat_2_Rot(JPL q))
//       cq, cp = the camera-IMU transform in use at that moment (the online estimate if calibration is on): HAMILTON quaternion of
//                R_CtoI and p_CinI, so that  T_G_C = T_G_I * T_I_C  (composed in scripts/openvins/openvins_tools.py)
//   Every frame is fed (no 'track_frequency' dropping: that rule exists only in the ROS callbacks); frames before the filter
//   initialised, and frames after the last IMU sample, get no line.
// Exit codes: 0 finished (also if few poses: see OV_STATS), 2 usage, 3 bad input data/config, 4 the filter never initialised.
// NOT COMPILED YET: written offline against rpng/open_vins @d0075dd.

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/time.h>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

#include <opencv2/opencv.hpp>

#include "core/VioManager.h"
#include "core/VioManagerOptions.h"
#include "state/State.h"
#include "utils/print.h"
#include "utils/quat_ops.h"
#include "utils/sensor_data.h"

using namespace ov_msckf;

struct Frame {
  double t;
  std::string path;
};

static double now_s() {
  struct timeval tv;
  gettimeofday(&tv, NULL);
  return tv.tv_sec + tv.tv_usec * 1e-6;
}

static std::string dirname_of(const std::string &p) {
  size_t k = p.find_last_of('/');
  return k == std::string::npos ? std::string(".") : p.substr(0, k);
}

static bool read_rgb(const std::string &file, std::vector<Frame> &out) {
  std::ifstream f(file.c_str());
  if (!f.good())
    return false;
  std::string dir = dirname_of(file), line;
  while (std::getline(f, line)) {
    if (line.empty() || line[0] == '#')
      continue;
    std::istringstream ss(line);
    Frame fr;
    std::string rel;
    if (!(ss >> fr.t >> rel))
      continue;
    fr.path = (!rel.empty() && rel[0] == '/') ? rel : dir + "/" + rel;
    out.push_back(fr);
  }
  return !out.empty();
}

static bool read_imu(const std::string &file, std::vector<ov_core::ImuData> &out) {
  std::ifstream f(file.c_str());
  if (!f.good())
    return false;
  std::string line;
  while (std::getline(f, line)) {
    if (line.empty() || line[0] == '#')
      continue;
    std::istringstream ss(line);
    double t, ax, ay, az, gx, gy, gz;
    if (!(ss >> t >> ax >> ay >> az >> gx >> gy >> gz))
      continue;
    ov_core::ImuData m;
    m.timestamp = t;
    m.am << ax, ay, az;   // accelerometer (specific force), m/s^2
    m.wm << gx, gy, gz;   // gyroscope, rad/s
    out.push_back(m);
  }
  return !out.empty();
}

int main(int argc, char **argv) {
  if (argc < 5) {
    fprintf(stderr, "usage: %s <estimator_config.yaml> <rgb.txt> <imu.txt> <out_poses.txt> [--max-frames N]\n", argv[0]);
    return 2;
  }
  std::string config_path = argv[1], rgb_path = argv[2], imu_path = argv[3], out_path = argv[4];
  int max_frames = 0;
  for (int i = 5; i < argc; i++) {
    if (!strcmp(argv[i], "--max-frames") && i + 1 < argc)
      max_frames = atoi(argv[++i]);
    else {
      fprintf(stderr, "unknown argument '%s'\n", argv[i]);
      return 2;
    }
  }

  std::vector<Frame> frames;
  std::vector<ov_core::ImuData> imu;
  if (!read_rgb(rgb_path, frames)) {
    fprintf(stderr, "cannot read frames from %s\n", rgb_path.c_str());
    return 3;
  }
  if (!read_imu(imu_path, imu)) {
    fprintf(stderr, "cannot read IMU samples from %s\n", imu_path.c_str());
    return 3;
  }
  if (max_frames > 0 && (int)frames.size() > max_frames)
    frames.resize(max_frames);
  for (size_t i = 1; i < frames.size(); i++)
    if (!(frames[i].t > frames[i - 1].t)) {
      fprintf(stderr, "frame times are not strictly increasing at row %zu\n", i);
      return 3;
    }
  for (size_t i = 1; i < imu.size(); i++)
    if (!(imu[i].timestamp > imu[i - 1].timestamp)) {
      fprintf(stderr, "IMU times are not strictly increasing at row %zu (run scripts/orbslam3/convert_imu.py first)\n", i);
      return 3;
    }

  // ---- configuration (as run_simulation.cpp, minus the simulator) ----------------------------------------------------------
  auto parser = std::make_shared<ov_core::YamlParser>(config_path);   // exits with a message if the file cannot be opened
  std::string verbosity = "INFO";
  parser->parse_config("verbosity", verbosity);
  ov_core::Printer::setPrintLevel(verbosity);
  VioManagerOptions params;
  params.print_and_load(parser);
  params.num_opencv_threads = 0;          // repeatability, as run_simulation
  params.use_multi_threading_pubs = false;
  params.use_multi_threading_subs = false;   // initialisation thread is joined: synchronous
  if (!parser->successful()) {
    fprintf(stderr, "unable to parse all parameters of %s (see the messages above)\n", config_path.c_str());
    return 3;
  }
  auto sys = std::make_shared<VioManager>(params);

  std::ofstream out(out_path.c_str());
  if (!out.good()) {
    fprintf(stderr, "cannot write %s\n", out_path.c_str());
    return 3;
  }
  out << std::setprecision(15);

  // ---- main loop: IMU stream drives the camera queue (ROS2Visualizer::callback_inertial) -----------------------------------
  const double t0 = now_s();
  size_t ci = 0;             // next camera frame
  int processed = 0, posed = 0, nonfinite = 0, unreadable = 0;
  double first_pose_t = -1;
  for (size_t k = 0; k < imu.size() && ci < frames.size(); k++) {
    sys->feed_measurement_imu(imu[k]);
    double dt_cam_imu = sys->get_state()->_calib_dt_CAMtoIMU->value()(0);
    double imu_in_cam_clock = imu[k].timestamp - dt_cam_imu;
    while (ci < frames.size() && frames[ci].t < imu_in_cam_clock) {
      cv::Mat img = cv::imread(frames[ci].path, cv::IMREAD_GRAYSCALE);   // MONO8 like cv_bridge::toCvShare(..., MONO8)
      if (img.empty()) {
        fprintf(stderr, "cannot read image %zu: %s\n", ci, frames[ci].path.c_str());
        unreadable++;
        ci++;
        continue;
      }
      ov_core::CameraData cam;
      cam.timestamp = frames[ci].t;
      cam.sensor_ids.push_back(0);
      cam.images.push_back(img);
      cam.masks.push_back(cv::Mat::zeros(img.rows, img.cols, CV_8UC1));   // use_mask == false
      sys->feed_measurement_camera(cam);
      processed++;

      std::shared_ptr<State> st = sys->get_state();
      if (sys->initialized() && st->_timestamp == cam.timestamp) {
        Eigen::Matrix3d R_ItoG = ov_core::quat_2_Rot(st->_imu->quat()).transpose();   // quat() is the JPL q_GtoI
        Eigen::Vector3d p_IinG = st->_imu->pos();
        Eigen::Matrix3d R_CtoI = ov_core::quat_2_Rot(st->_calib_IMUtoCAM.at(0)->quat()).transpose();   // stored: q_ItoC
        Eigen::Vector3d p_CinI = -R_CtoI * st->_calib_IMUtoCAM.at(0)->pos();                           // stored: p_IinC
        Eigen::Quaterniond qI(R_ItoG), qC(R_CtoI);
        qI.normalize();
        qC.normalize();
        bool ok = std::isfinite(p_IinG.norm()) && std::isfinite(qI.coeffs().norm()) && std::isfinite(p_CinI.norm()) &&
                  std::isfinite(qC.coeffs().norm());
        if (ok) {
          out << ci << " " << p_IinG.transpose() << " " << qI.x() << " " << qI.y() << " " << qI.z() << " " << qI.w() << " " << qC.x() << " "
              << qC.y() << " " << qC.z() << " " << qC.w() << " " << p_CinI.transpose() << "\n";
          if (posed == 0)
            first_pose_t = cam.timestamp;
          posed++;
        } else {
          nonfinite++;
        }
      }
      ci++;
    }
  }
  out.close();
  double wall = now_s() - t0;
  printf("OV_STATS frames=%zu imu=%zu processed=%d posed=%d nonfinite=%d unreadable=%d initialized=%d first_pose_t_rel=%.3f wall_s=%.1f\n",
         frames.size(), imu.size(), processed, posed, nonfinite, unreadable, sys->initialized() ? 1 : 0,
         first_pose_t >= 0 ? first_pose_t - frames[0].t : -1.0, wall);
  if (unreadable > 0)
    return 3;
  return posed > 0 ? 0 : 4;
}
