// COMTRADE (IEEE C37.111-1991/1999/2013) reader.
//
// Supports CFG + DAT pairs with ASCII, BINARY (16-bit), BINARY32 and FLOAT32
// data. The combined .CFF container from the 2013 revision is not supported.
#pragma once

#include <cstdint>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

namespace rea {

class ComtradeError : public std::runtime_error {
 public:
  using std::runtime_error::runtime_error;
};

enum class DataFormat { Ascii, Binary, Binary32, Float32 };

std::string to_string(DataFormat f);

struct AnalogChannel {
  int index = 0;
  std::string id;
  std::string phase;
  std::string ccbm;
  std::string unit;
  double a = 1.0;  // value = a * raw + b
  double b = 0.0;
  double skew_us = 0.0;
  double min = 0.0;
  double max = 0.0;
  double primary = 1.0;    // transformer ratio, primary factor (1999+)
  double secondary = 1.0;  // transformer ratio, secondary factor (1999+)
  char ps = 'S';           // 'P' if a*raw+b is in primary units, 'S' if secondary
  bool has_ratio = false;  // true when primary/secondary/PS were present
};

struct DigitalChannel {
  int index = 0;
  std::string id;
  std::string phase;
  std::string ccbm;
  int normal_state = 0;
};

struct SampleRate {
  double rate_hz = 0.0;
  long long end_sample = 0;
};

struct Timestamp {
  std::string text;  // exactly as written in the CFG
  int year = 0, month = 0, day = 0, hour = 0, minute = 0;
  double second = 0.0;
  bool valid = false;
  // Seconds since 1970-01-01 (proleptic Gregorian, no time zone handling).
  double epoch_seconds() const;
};

struct Config {
  std::string station;
  std::string device;
  int rev_year = 1991;
  std::vector<AnalogChannel> analogs;
  std::vector<DigitalChannel> digitals;
  double line_freq_hz = 60.0;
  std::vector<SampleRate> rates;  // empty or rate 0 means "use DAT timestamps"
  Timestamp start;
  Timestamp trigger;
  DataFormat format = DataFormat::Ascii;
  double time_mult = 1.0;
  long long declared_samples() const;  // last endsamp, 0 if unknown
};

struct Record {
  Config cfg;
  std::vector<double> time_s;                      // seconds from the first sample
  std::vector<std::vector<double>> analog;         // [channel][sample] = a*raw+b
  std::vector<std::vector<std::uint8_t>> digital;  // [channel][sample] in {0,1}
  std::vector<std::string> warnings;

  std::size_t num_samples() const { return time_s.size(); }
  // Sampling rate of the first rate segment, or estimated from timestamps.
  double sample_rate_hz() const;
  // Trigger time relative to the first sample, if both CFG timestamps are valid.
  bool trigger_offset_s(double& out) const;
};

// Parse the text of a .CFG file. Throws ComtradeError with a line number on failure.
Config parse_cfg(const std::string& text);

// Parse DAT content (ASCII text or raw binary bytes) for an already parsed CFG.
Record parse_dat(const Config& cfg, const std::string& bytes);

// Load <name>.cfg and the matching <name>.dat (case-insensitive extension).
Record load_record(const std::filesystem::path& cfg_path);

std::string read_file(const std::filesystem::path& p);

}  // namespace rea
