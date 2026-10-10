cmake_minimum_required(VERSION 3.16)
project(rea_core VERSION 0.1.0 LANGUAGES CXX)

# C++17 so the core builds with GCC 11 (JetPack 6 / Ubuntu 22.04) and newer.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(REA_BUILD_TESTS "Build the GoogleTest suite" ON)
option(REA_WERROR "Treat compiler warnings as errors" OFF)

add_library(rea_core
  src/comtrade.cpp
  src/dsp.cpp
  src/curves.cpp
  src/settings.cpp
  src/facts.cpp
  src/analyzer.cpp
)
target_include_directories(rea_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)
if(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(rea_core PRIVATE -Wall -Wextra -Wpedantic -Wshadow)
  if(REA_WERROR)
    target_compile_options(rea_core PRIVATE -Werror)
  endif()
endif()

add_executable(rea-core apps/rea_core.cpp)
target_link_libraries(rea-core PRIVATE rea_core)
install(TARGETS rea-core RUNTIME DESTINATION bin)

if(REA_BUILD_TESTS)
  enable_testing()
  find_package(GTest QUIET)
  if(NOT GTest_FOUND)
    include(FetchContent)
    FetchContent_Declare(googletest
      GIT_REPOSITORY https://github.com/google/googletest.git
      GIT_TAG v1.15.2
      GIT_SHALLOW TRUE)
    set(INSTALL_GTEST OFF CACHE BOOL "" FORCE)
    set(BUILD_GMOCK OFF CACHE BOOL "" FORCE)
    FetchContent_MakeAvailable(googletest)
  endif()
  if(TARGET GTest::gtest_main)
    set(REA_GTEST_MAIN GTest::gtest_main)
  else()
    set(REA_GTEST_MAIN GTest::Main)
  endif()

  add_executable(rea_tests
    tests/test_comtrade.cpp
    tests/test_dsp.cpp
    tests/test_curves.cpp
    tests/test_settings.cpp
    tests/test_facts.cpp
    tests/test_analyzer.cpp
    tests/test_field_cases.cpp
  )
  target_link_libraries(rea_tests PRIVATE rea_core ${REA_GTEST_MAIN})
  include(GoogleTest)
  gtest_discover_tests(rea_tests)
endif()
