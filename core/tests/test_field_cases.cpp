// Situations met in real relay records rather than in clean simulations: ACB phase
// rotation (and a relay set for the wrong one), voltage channels with no VTs behind
// them, faults shorter than the phasor window, records that start inside the fault,
// a trip that comes after the fault has gone, no INOM setting, and whole-ampere
// quantization.
#include <gtest/gtest.h>

#include <cmath>
#include <string>

#include "rea/analyzer.hpp"
#include "test_support.hpp"

using namespace rea;
using namespace rea::test;

namespace {

Settings settings(const std::string& extra = "") {
  return Settings::parse("INOM := 5  VNOM := 66.4  Z1ANG := 82  Z1MAG := 4.0\n" + extra);
}

const Fact* find(const AnalysisResult& r, const std::string& id) { return r.facts.find(id); }

std::string text(const AnalysisResult& r, const std::string& id) {
  const Fact* f = r.facts.find(id);
  return f == nullptr ? "<none>" : f->text;
}

ThreePhase swap_bc(const ThreePhase& x) { return {x.a, x.c, x.b}; }

// The same physical event seen by a system with ACB rotation: relabel B <-> C.
EventSpec acb(EventSpec e) {
  e.v_pre = swap_bc(e.v_pre);
  e.i_pre = swap_bc(e.i_pre);
  e.v_flt = swap_bc(e.v_flt);
  e.i_flt = swap_bc(e.i_flt);
  return e;
}

bool has_warning(const AnalysisResult& r, const std::string& needle) {
  for (const auto& w : r.facts.warnings()) {
    if (w.find(needle) != std::string::npos) return true;
  }
  return false;
}

}  // namespace

TEST(FieldCases, AcbRotationClassifiesWithRelabeledPhases) {
  // An AB fault in an ABC system is a CA fault once B and C are relabeled.
  const struct {
    const char* abc_type;
    const char* acb_type;
  } cases[] = {{"AB", "CA"}, {"BG", "CG"}, {"ABG", "CAG"}, {"BC", "BC"}, {"AG", "AG"}};
  for (const auto& c : cases) {
    EventSpec e = acb(fault_event(c.abc_type, 8.0, true));
    e.t_return = 0.3;
    const AnalysisResult r = analyze(build_record(e), settings("PHROT := ACB"));
    EXPECT_EQ(to_string(r.fault_type), c.acb_type) << c.abc_type;
    EXPECT_EQ(r.direction, Direction::Forward) << c.abc_type;
    EXPECT_EQ(text(r, "record.rotation"), "ACB");
    EXPECT_EQ(text(r, "assessment.rotation"), "matches the PHROT setting");
  }
}

TEST(FieldCases, WrongRotationSettingIsReportedAndMeasurementWins) {
  EventSpec e = acb(fault_event("AB", 8.0, true));
  e.t_return = 0.3;
  const AnalysisResult r = analyze(build_record(e), settings("PHROT := ABC"));
  EXPECT_EQ(to_string(r.fault_type), "CA");  // physics follows the measured rotation
  const Fact* f = find(r, "assessment.rotation");
  ASSERT_NE(f, nullptr);
  EXPECT_EQ(f->priority, 1);
  EXPECT_NE(f->text.find("PHROT := ABC"), std::string::npos);
  EXPECT_TRUE(has_warning(r, "positive and negative sequence swapped"));
}

TEST(FieldCases, VoltageChannelsWithoutSignalAreTreatedAsAbsent) {
  EventSpec e = fault_event("BC", 8.0, true);
  e.v_pre = e.v_flt = balanced(0.0, 0.0);
  e.t_return = 0.3;
  const AnalysisResult r = analyze(build_record(e), settings());
  EXPECT_EQ(to_string(r.fault_type), "BC");
  const Fact* d = find(r, "fault.direction");
  ASSERT_NE(d, nullptr);
  EXPECT_EQ(d->kind, FactKind::Missing);
  EXPECT_NE(d->reason.find("no signal"), std::string::npos);
}

TEST(FieldCases, FaultShorterThanThePhasorWindow) {
  // A 1.3-cycle C-A flashover that clears itself; no trip. A self-clearing arc goes
  // out at a current zero, so the current returns to load with almost no DC tail.
  EventSpec e = fault_event("CA", 6.0, true);
  e.t_return = e.t_fault + 1.3 / 60.0;
  e.tau_dc = 0.002;
  const AnalysisResult r = analyze(build_record(e), settings());
  EXPECT_EQ(to_string(r.fault_type), "CA");
  const Fact* dur = find(r, "fault.duration_cycles");
  ASSERT_NE(dur, nullptr);
  EXPECT_NEAR(dur->number, 1.3, 0.3);
  EXPECT_NE(text(r, "fault.end_cause").find("before any local trip"), std::string::npos);
  EXPECT_TRUE(has_warning(r, "shorter than the phasor window"));
}

TEST(FieldCases, TripAfterTheFaultHasEnded) {
  // The record starts inside a C-A fault that other protection clears at 0.1 s; the
  // relay trips 0.3 s later on load (for example a misapplied setting).
  EventSpec e = fault_event("CA", 6.0, true);
  e.fault_from_start = true;
  e.t_return = 0.1;
  e.t_trip = 0.4;
  const AnalysisResult r = analyze(build_record(e), settings());
  EXPECT_FALSE(r.inception_in_record);
  EXPECT_EQ(to_string(r.fault_type), "CA");  // classified against the post-fault load
  EXPECT_EQ(r.relay_assessment, "tripped after the fault had ended");
  const Fact* late = find(r, "relay.trip_after_fault_end_ms");
  ASSERT_NE(late, nullptr);
  EXPECT_NEAR(late->number, 300.0, 1000.0 / 60.0);
  EXPECT_TRUE(has_warning(r, "post-fault load as the reference"));
}

TEST(FieldCases, WithoutInomThresholdsFollowTheRecord) {
  // Currents a hundredth of a 5 A rating: below the fixed 0.1 x INOM threshold, but
  // clearly a fault relative to the record's own load.
  EventSpec e = fault_event("BG", 0.08, true);
  e.i_pre = balanced(0.02, -20.0);
  e.i_flt = add(e.i_pre, make_fault("BG", 0.08, true).di);
  e.t_return = 0.3;
  const Record rec = build_record(e);
  const AnalysisResult fixed = analyze(rec, settings());
  EXPECT_FALSE(fixed.fault_detected);
  const AnalysisResult scaled = analyze(rec, Settings::parse("VNOM := 66.4  Z1ANG := 82  Z1MAG := 4.0"));
  EXPECT_EQ(to_string(scaled.fault_type), "BG");
  EXPECT_TRUE(has_warning(scaled, "INOM not set"));
}

TEST(FieldCases, WholeAmpereQuantizationDoesNotFakeAnOnset) {
  // Primary amperes recorded as integers: the cycle-to-cycle difference of the load
  // is 0 or 1 A, which once gave a zero noise estimate and an onset in the load.
  EventSpec e = fault_event("CA", 300.0, true);
  e.i_pre = balanced(85.0, -20.0);
  e.i_flt = add(e.i_pre, make_fault("CA", 300.0, true).di);
  e.t_return = 0.3;
  e.with_voltage = false;
  Record rec = build_record(e);
  for (auto& ch : rec.analog) {
    for (auto& x : ch) x = std::round(x);
  }
  const AnalysisResult r = analyze(rec, settings());
  ASSERT_TRUE(r.inception_s.has_value());
  EXPECT_NEAR(*r.inception_s, e.t_fault, 2.0 / 1920.0);
  EXPECT_EQ(to_string(r.fault_type), "CA");
}

TEST(FieldCases, BreakerClosingBeforeTheDisturbanceIsReported) {
  // Current appears two cycles after 52A rises (a motor start, or a switch onto a fault).
  EventSpec e;
  e.i_pre = balanced(0.0, 0.0);
  e.i_flt = balanced(6.0, -30.0);
  e.v_flt = balanced(63.0, 0.0);
  e.t_return = 0.45;
  Record rec = build_record(e);
  for (std::size_t k = 0; k < rec.time_s.size(); ++k) {
    rec.digital[1][k] = rec.time_s[k] >= e.t_fault - 2.0 / 60.0 ? 1 : 0;
  }
  const AnalysisResult r = analyze(rec, settings());
  const Fact* f = find(r, "fault.after_breaker_close_ms");
  ASSERT_NE(f, nullptr);
  EXPECT_NEAR(f->number, 2000.0 / 60.0, 1.0);
  EXPECT_EQ(f->priority, 1);
  EXPECT_TRUE(has_warning(r, "energization"));
}
