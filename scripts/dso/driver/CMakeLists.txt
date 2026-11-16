# Builds dso_headless against an already built libdso.a (DSO built WITHOUT Pangolin). Called by scripts/setup/dso.sh:
#   cmake -S scripts/dso/driver -B <build> -DDSO_DIR=~/src/dso -DDSO_MARCH=native -DCMAKE_INSTALL_PREFIX=<DSO_PREFIX>
# Mirrors the include/link lines of DSO's own CMakeLists.txt @7b0c99f (include dirs src, thirdparty/Sophus, thirdparty/sse2neon,
# Eigen3, suitesparse; link dso boost_system cxsparse boost_thread OpenCV; -DENABLE_SSE). NOT BUILT YET (prepared offline).
cmake_minimum_required(VERSION 3.10)
project(dso_headless CXX)

if(NOT DSO_DIR)
  message(FATAL_ERROR "pass -DDSO_DIR=<DSO source tree with build/lib/libdso.a>")
endif()
if(NOT DSO_MARCH)
  set(DSO_MARCH native)
endif()
if(NOT DSO_CXX_STD)
  set(DSO_CXX_STD c++0x)
endif()
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

# Same ABI-relevant flags as the library (Eigen alignment must agree). -fno-access-control: the driver reads the private members
# FullSystem::allFrameHistory / allKeyFramesHistory to write one pose per frame (upstream only offers printResult()).
set(CMAKE_CXX_FLAGS "-O3 -g -std=${DSO_CXX_STD} -march=${DSO_MARCH} -fno-access-control")
add_definitions(-DENABLE_SSE)

find_package(Eigen3 REQUIRED)
find_package(OpenCV REQUIRED)
find_package(Boost COMPONENTS system thread REQUIRED)
find_library(CXSPARSE_LIB cxsparse REQUIRED)
find_library(DSO_LIB dso PATHS "${DSO_DIR}/build/lib" NO_DEFAULT_PATH REQUIRED)

if(NOT EIGEN3_INCLUDE_DIR)
  set(EIGEN3_INCLUDE_DIR /usr/include/eigen3)
endif()
include_directories(
  ${DSO_DIR}/src
  ${DSO_DIR}/thirdparty/Sophus
  ${DSO_DIR}/thirdparty/sse2neon
  ${EIGEN3_INCLUDE_DIR}
  /usr/include/suitesparse
  ${OpenCV_INCLUDE_DIRS}
  ${Boost_INCLUDE_DIRS})

add_executable(dso_headless dso_headless.cc)
target_link_libraries(dso_headless ${DSO_LIB} boost_system ${CXSPARSE_LIB} boost_thread ${OpenCV_LIBS} pthread)
install(TARGETS dso_headless RUNTIME DESTINATION bin)
