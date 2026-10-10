// Event analysis: turns a COMTRADE record plus relay settings into a fact sheet.
//
// Pipeline (see docs/physics.md for the math):
//   1. channel mapping and scaling to secondary amps / volts
//   2. fixed-reference phasors (cosine or DFT filter)
//   3. fault inception from cycle-to-cycle sample differences
//   4. fault type from incremental phase-to-phase currents and the angle of I2/I0
//   5. direction from the negative-sequence impedance z2 (incremental positive
//      sequence for balanced faults)
//   6. relay operate time, per-pole breaker interruption, fault duration
//   7. expected operate time by replaying the measured currents through the set
//      50/51 elements (IEEE C37.112 integral), and an expected-vs-actual verdict
#pragma once

#include <optional>
#include <string>
#include <vector>

#include "rea/comtrade.hpp"
#include "rea/dsp.hpp"
#include "rea/facts.hpp"
#include "rea/settings.hpp"

namespace rea {

enum class FaultType { None, AG, BG, CG, AB, BC, CA, ABG, BCG, CAG, ABC };
enum class Direction { Unknown, Forward, Reverse };

std::string to_string(FaultType t);
std::string describe(FaultType t);
std::string to_string(Direction d);

struct AnalyzerOptions {
  FilterType filter = FilterType::Cosine;
};

struct ChannelMap {
  int ia = -1, ib = -1, ic = -1;
  int va = -1, vb = -1, vc = -1;
  int trip = -1;
  int breaker = -1;
  bool has_currents() const { return ia >= 0 && ib >= 0 && ic >= 0; }
  bool has_voltages() const { return va >= 0 && vb >= 0 && vc >= 0; }
};

ChannelMap map_channels(const Record& rec, const Settings& settings,
                        std::vector<std::string>* notes = nullptr);

struct ElementPrediction {
  std::string element;  // "50P1", "51P1" or "51G1"
  double time_s = 0.0;  // operate time measured from fault inception
  bool extrapolated = false;
};

struct AnalysisResult {
  FactSheet facts;
  bool fault_detected = false;
  bool inception_in_record = false;
  std::optional<double> inception_s;  // all times are seconds from the first sample
  FaultType fault_type = FaultType::None;
  Direction direction = Direction::Unknown;
  std::optional<double> trip_s;
  std::optional<double> breaker_clear_s;  // last pole interrupted
  std::optional<double> fault_end_s;      // fault removed by any means
  std::optional<ElementPrediction> expected;
  std::string relay_assessment = "undetermined";
  std::string breaker_assessment = "undetermined";
};

AnalysisResult analyze(const Record& rec, const Settings& settings,
                       const AnalyzerOptions& options = {});

}  // namespace rea
