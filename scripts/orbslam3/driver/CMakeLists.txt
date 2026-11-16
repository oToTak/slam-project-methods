# Builds orbslam3_tum against an already built ORB-SLAM3 tree. Called by scripts/setup/orbslam3.sh:
#   cmake -S scripts/orbslam3/driver -B <build> -DORB3_DIR=~/src/ORB_SLAM3 -DORB3_CXX_STD=c++14 \
#         -DCMAKE_PREFIX_PATH=<pangolin prefix> -DCMAKE_INSTALL_PREFIX=<ORB3_PREFIX>
# Mirrors the include/link lines of ORB-SLAM3's own CMakeLists.txt @4452a3c (OpenCV >= 4.4, Eigen3, Pangolin, DBoW2, g2o,
# -lboost_serialization -lcrypto). NOT BUILT YET (prepared offline).
cmake_minimum_required(VERSION 3.10)
project(orbslam3_tum CXX)

if(NOT ORB3_DIR)
  message(FATAL_ERROR "pass -DORB3_DIR=<built ORB-SLAM3 source tree>")
endif()
if(NOT ORB3_CXX_STD)
  set(ORB3_CXX_STD c++14)
endif()
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# Same flags as the library (Eigen alignment must agree between the library and this binary).
set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Wall -O3 -std=${ORB3_CXX_STD}")
set(CMAKE_CXX_FLAGS_RELEASE "${CMAKE_CXX_FLAGS_RELEASE} -march=native")

find_package(OpenCV 4.4 REQUIRED)
find_package(Eigen3 3.1.0 REQUIRED)
find_package(Pangolin REQUIRED)

foreach(lib "${ORB3_DIR}/lib/libORB_SLAM3.so"
            "${ORB3_DIR}/Thirdparty/DBoW2/lib/libDBoW2.so"
            "${ORB3_DIR}/Thirdparty/g2o/lib/libg2o.so")
  if(NOT EXISTS "${lib}")
    message(FATAL_ERROR "missing ${lib}: build ORB-SLAM3 first (scripts/setup/orbslam3.sh)")
  endif()
endforeach()

include_directories(
  ${ORB3_DIR}
  ${ORB3_DIR}/include
  ${ORB3_DIR}/include/CameraModels
  ${ORB3_DIR}/Thirdparty/Sophus
  ${EIGEN3_INCLUDE_DIR}
  ${OpenCV_INCLUDE_DIRS}
  ${Pangolin_INCLUDE_DIRS})

add_executable(orbslam3_tum orbslam3_tum.cc)
target_link_libraries(orbslam3_tum
  ${ORB3_DIR}/lib/libORB_SLAM3.so
  ${ORB3_DIR}/Thirdparty/DBoW2/lib/libDBoW2.so
  ${ORB3_DIR}/Thirdparty/g2o/lib/libg2o.so
  ${OpenCV_LIBS}
  ${EIGEN3_LIBS}
  ${Pangolin_LIBRARIES}
  -lboost_serialization
  -lcrypto)

# Find the shared libraries at run time without LD_LIBRARY_PATH (run.sh sets it anyway).
set(CMAKE_INSTALL_RPATH "${ORB3_DIR}/lib;${ORB3_DIR}/Thirdparty/DBoW2/lib;${ORB3_DIR}/Thirdparty/g2o/lib")
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH TRUE)
install(TARGETS orbslam3_tum RUNTIME DESTINATION bin)
