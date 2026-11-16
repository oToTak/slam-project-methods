# Builds ov_ucu_driver against an already built libov_msckf_lib.so (OpenVINS built with -DENABLE_ROS=OFF). Called by scripts/setup/openvins.sh:
#   cmake -S scripts/openvins/driver -B <build> -DOV_DIR=~/src/open_vins -DCMAKE_INSTALL_PREFIX=<OV_PREFIX>
# Mirrors the include/link lines of ov_msckf/cmake/ROS1.cmake @d0075dd for the ROS-free case: include dirs ov_msckf/src, ov_core/src,
# ov_init/src (the lib is built from the ov_core and ov_init sources too, nothing needs `make install`), Eigen3, Boost, Ceres, OpenCV.
# NOT BUILT YET (prepared offline).
cmake_minimum_required(VERSION 3.10)
project(ov_ucu_driver CXX)

if(NOT OV_DIR)
  message(FATAL_ERROR "pass -DOV_DIR=<OpenVINS source tree with ov_msckf/build/libov_msckf_lib.so>")
endif()
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# Same standard and compile definitions as the library (ov_msckf/CMakeLists.txt: C++14; ROS1.cmake: -DROS_AVAILABLE=0;
# -DENABLE_ARUCO_TAGS=0 because the library is built without aruco).
set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
add_definitions(-DROS_AVAILABLE=0 -DENABLE_ARUCO_TAGS=0)

find_package(Eigen3 REQUIRED)
find_package(OpenCV 3 QUIET)
if(NOT OpenCV_FOUND)
  find_package(OpenCV 4 REQUIRED)
endif()
find_package(Boost REQUIRED COMPONENTS system filesystem thread date_time)
find_package(Ceres REQUIRED)
find_library(OV_LIB ov_msckf_lib PATHS "${OV_DIR}/ov_msckf/build" NO_DEFAULT_PATH REQUIRED)

if(NOT EIGEN3_INCLUDE_DIR)
  set(EIGEN3_INCLUDE_DIR /usr/include/eigen3)
endif()
include_directories(
  ${OV_DIR}/ov_msckf/src
  ${OV_DIR}/ov_core/src
  ${OV_DIR}/ov_init/src
  ${EIGEN3_INCLUDE_DIR}
  ${Boost_INCLUDE_DIRS}
  ${CERES_INCLUDE_DIRS}
  ${OpenCV_INCLUDE_DIRS})

add_executable(ov_ucu_driver ov_ucu_driver.cc)
target_link_libraries(ov_ucu_driver ${OV_LIB} ${Boost_LIBRARIES} ${OpenCV_LIBS} ${OpenCV_LIBRARIES} ${CERES_LIBRARIES} pthread)

# find the shared library at run time without LD_LIBRARY_PATH (run.sh sets it anyway)
set(CMAKE_INSTALL_RPATH "${OV_DIR}/ov_msckf/build")
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH TRUE)
install(TARGETS ov_ucu_driver RUNTIME DESTINATION bin)
