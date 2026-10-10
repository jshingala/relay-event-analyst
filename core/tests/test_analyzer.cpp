#include <gtest/gtest.h>

#include <cmath>
#include <string>

#include "rea/analyzer.hpp"
#include "rea/curves.hpp"
#include "test_support.hpp"

using namespace rea;
using namespace rea::test;

namespace {

Settings base_settings(const std::string& extra = "") {
  return Settings::parse("INOM := 5  VNOM := 66.4  Z1ANG := 82  Z1MAG := 4.0\n" + extra);
}

const Fact& fact(const AnalysisResult& r, const std::string& id) {
  const Fact* f = r.facts.find(id);
  if (f == nullptr) throw std::runtime_error("missing fact " + id);
  return *f;
}

double max_fault_rms(const EventSpec& e) {
  return std::max({std::abs(e.i_flt.a), std::abs(e.i_flt.b), std::abs(e.i_flt.c)});
}

const char* kTypes[] = {"AG", "BG", "CG", "AB", "BC", "CA", "ABG", "BCG", "CAG", "ABC"};

}  // namespace

class FaultTypeTest : public ::testing::TestWithParam<const char*> {};

TEST_P(FaultTypeTest, ForwardFaultTypeDirectionAndInception) {
  EventSpec e = fault_event(GetParam(), 8.0, true);
  e.t_trip = 0.2;
  e.t_open = 0.235;
  const AnalysisResult r = analyze(build_record(e), base_settings());
  EXPECT_EQ(to_string(r.fault_type), GetParam());
  EXPECT_EQ(r.direction, Direction::Forward);
  ASSERT_TRUE(r.inception_s.has_value());
  EXPECT_NEAR(*r.inception_s, e.t_fault, 1.0 / 1920.0 + 1e-12);
  const Fact* z = r.facts.find("fault.z2_ohm");
  if (z == nullptr) z = r.facts.find("fault.dz1_ohm");
  ASSERT_NE(z, nullptr);
  EXPECT_LT(z->number, 0.0) << "forward faults measure the source impedance behind the relay (negative)";
}

TEST_P(FaultTypeTest, ReverseFaultDirection) {
  EventSpec e = fault_event(GetParam(), 8.0, false);
  e.t_return = 0.3;
  const AnalysisResult r = analyze(build_record(e), base_settings());
  EXPECT_EQ(to_string(r.fault_type), GetParam());
  EXPECT_EQ(r.direction, Direction::Reverse);
}

TEST_P(FaultTypeTest, DftFilterAlsoClassifies) {
  EventSpec e = fault_event(GetParam(), 6.0, true);
  e.t_return = 0.3;
  AnalyzerOptions opt;
  opt.filter = FilterType::Dft;
  const AnalysisResult r = analyze(build_record(e), base_settings(), opt);
  EXPECT_EQ(to_string(r.fault_type), GetParam());
  EXPECT_EQ(r.direction, Direction::Forward);
}

TEST_P(FaultTypeTest, RobustToNoiseOffNominalFrequencyAndLowSampleRate) {
  EventSpec e = fault_event(GetParam(), 5.0, true);
  e.f = 59.92;
  e.spc = 16;
  e.noise = 0.02;
  e.t_return = 0.35;
  const AnalysisResult r = analyze(build_record(e), base_settings());
  EXPECT_EQ(to_string(r.fault_type), GetParam());
  EXPECT_EQ(r.direction, Direction::Forward);
  ASSERT_TRUE(r.inception_s.has_value());
  EXPECT_NEAR(*r.inception_s, e.t_fault, 2.0 / (16 * 60.0) + 1e-12);
}

INSTANTIATE_TEST_SUITE_P(AllTen, FaultTypeTest, ::testing::ValuesIn(kTypes));

TEST(Analyzer, DoubleLineToGroundWithHighGroundResistance) {
  // 3Rg = 8 ohm swings angle(I2/I0) toward the neighbouring sector; the dominant
  // loop must still name the faulted pair.
  for (const char* type : {"BCG", "CAG", "ABG"}) {
    for (double three_rg : {2.0, 8.0}) {
      EventSpec e = fault_event(type, 8.0, true, three_rg);
      e.t_return = 0.3;
      const AnalysisResult r = analyze(build_record(e), base_settings());
      EXPECT_EQ(to_string(r.fault_type), type) << "3Rg=" << three_rg;
    }
    // With 3Rg = 20 ohm the ground current is ~4% of the phase current: the fault is
    // reported as phase-to-phase, but the faulted pair must still be right.
    EventSpec e = fault_event(type, 8.0, true, 20.0);
    e.t_return = 0.3;
    const AnalysisResult r = analyze(build_record(e), base_settings());
    EXPECT_EQ(to_string(r.fault_type), std::string(type).substr(0, 2));
  }
}

TEST(Analyzer, FactsUseIds) {
  // fault.dz1_ohm only exists for balanced faults; fact() must throw for absent ids.
  EventSpec e = fault_event("AG", 8.0);
  e.t_return = 0.3;
  const AnalysisResult r = analyze(build_record(e), base_settings());
  EXPECT_THROW(fact(r, "fault.dz1_ohm"), std::runtime_error);
  EXPECT_EQ(fact(r, "fault.type").text, "AG");
  EXPECT_EQ(fact(r, "fault.type_description").text, "A-phase-to-ground");
}

TEST(Analyzer, TripAndBreakerTiming) {
  EventSpec e = fault_event("BG", 10.0);
  e.t_trip = 0.25;
  e.t_open = 0.25 + 2.0 / 60.0;
  const AnalysisResult r = analyze(build_record(e), base_settings());
  ASSERT_TRUE(r.trip_s.has_value());
  EXPECT_NEAR(*r.trip_s, 0.25, 1.0 / 1920.0);
  EXPECT_NEAR(fact(r, "relay.operate_ms").number, 150.0, 0.6);
  ASSERT_TRUE(r.breaker_clear_s.has_value());
  EXPECT_GE(*r.breaker_clear_s, *e.t_open - 1e-12);
  EXPECT_LE(*r.breaker_clear_s, *e.t_open + 0.5 / 60.0 + 2.0 / 1920.0);
  const double interrupt_ms = fact(r, "breaker.interrupt_ms").number;
  EXPECT_NEAR(interrupt_ms, (*r.breaker_clear_s - *r.trip_s) * 1000.0, 0.01);
  EXPECT_EQ(r.breaker_assessment, "normal");
  EXPECT_EQ(fact(r, "fault.end_cause").text, "local breaker interrupted the current");
  EXPECT_NEAR(fact(r, "breaker.52a_open_after_trip_ms").number, 2000.0 / 60.0, 0.6);
}

TEST(Analyzer, SlowBreakerIsFlagged) {
  EventSpec e = fault_event("CA", 10.0);
  e.t_trip = 0.2;
  e.t_open = 0.2 + 8.0 / 60.0;
  const AnalysisResult r = analyze(build_record(e), base_settings("BKRMAX := 5\n"));
  EXPECT_EQ(r.breaker_assessment, "slow");
}

TEST(Analyzer, PredictsInverseTimeTripAndJudgesIt) {
  EventSpec e = fault_event("ABC", 12.0);
  const double pickup = 3.0;
  const double m = max_fault_rms(e) / pickup;
  const double t_curve = operate_time_s(Curve::U3, 1.0, m);
  e.duration_s = 1.2;
  e.t_trip = e.t_fault + t_curve;
  e.t_open = *e.t_trip + 0.04;
  const auto s = base_settings("51P1P := 3.00  51P1C := U3  51P1TD := 1.00\n");
  const AnalysisResult r = analyze(build_record(e), s);
  ASSERT_TRUE(r.expected.has_value());
  EXPECT_EQ(r.expected->element, "51P1");
  EXPECT_FALSE(r.expected->extrapolated);
  // Replay lags the closed form only by the phasor-filter ramp (under 1.25 cycles).
  EXPECT_GT(r.expected->time_s, t_curve);
  EXPECT_LT(r.expected->time_s, t_curve + 1.25 / 60.0);
  EXPECT_EQ(r.relay_assessment, "operated as expected");
  EXPECT_NEAR(fact(r, "expected.curve_time_51p_ms").number, t_curve * 1000.0, 0.6);
}

TEST(Analyzer, SlowRelayIsFlagged) {
  EventSpec e = fault_event("AG", 12.0);
  const double t_curve = operate_time_s(Curve::U4, 2.0, max_fault_rms(e) / 2.0);
  e.duration_s = 2.0;
  e.t_trip = e.t_fault + 2.0 * t_curve;
  e.t_open = *e.t_trip + 0.04;
  const AnalysisResult r =
      analyze(build_record(e), base_settings("51P1P := 2.00  51P1C := U4  51P1TD := 2.00\n"));
  EXPECT_EQ(r.relay_assessment, "slower than expected");
  EXPECT_GT(fact(r, "assessment.deviation_ms").number, fact(r, "assessment.tolerance_ms").number);
}

TEST(Analyzer, InstantaneousElementWins) {
  EventSpec e = fault_event("BC", 20.0);
  e.t_trip = e.t_fault + 1.0 / 60.0;
  e.t_open = *e.t_trip + 0.04;
  const AnalysisResult r = analyze(
      build_record(e), base_settings("50P1P := 10.0  51P1P := 3.00  51P1C := U3  51P1TD := 2.00\n"));
  ASSERT_TRUE(r.expected.has_value());
  EXPECT_EQ(r.expected->element, "50P1");
  EXPECT_LT(r.expected->time_s, 1.25 / 60.0);
  EXPECT_EQ(r.relay_assessment, "operated as expected");
}

TEST(Analyzer, FailureToTripWhenFaultClearedElsewhere) {
  EventSpec e = fault_event("BG", 10.0);
  e.duration_s = 1.5;
  e.t_return = 1.2;  // backup protection removes the fault, local relay never trips
  const AnalysisResult r =
      analyze(build_record(e), base_settings("51P1P := 3.00  51P1C := U3  51P1TD := 1.00\n"));
  EXPECT_EQ(r.relay_assessment, "failed to trip");
  ASSERT_TRUE(r.fault_end_s.has_value());
  EXPECT_NEAR(*r.fault_end_s, 1.2, 1.0 / 60.0);
}

TEST(Analyzer, DirectionalSupervision) {
  const auto s = base_settings("51P1P := 3.00  51P1C := U3  51P1TD := 1.00  DIR := F\n");
  EventSpec rev = fault_event("AG", 10.0, false);
  rev.t_return = 0.3;
  EXPECT_EQ(analyze(build_record(rev), s).relay_assessment, "correctly did not trip");
  rev.t_trip = 0.25;
  EXPECT_EQ(analyze(build_record(rev), s).relay_assessment,
            "tripped for a fault outside its directional zone");
}

TEST(Analyzer, FaultAlreadyPresentAtRecordStart) {
  EventSpec e = fault_event("AG", 8.0);
  e.fault_from_start = true;
  e.t_trip = 0.05;
  e.t_open = 0.09;
  const AnalysisResult r = analyze(build_record(e), base_settings());
  EXPECT_FALSE(r.inception_in_record);
  EXPECT_EQ(fact(r, "relay.operate_ms").kind, FactKind::Missing);
  EXPECT_EQ(fact(r, "fault.inception_ms").kind, FactKind::Missing);
  EXPECT_EQ(to_string(r.fault_type), "AG");  // still classified from total quantities
}

TEST(Analyzer, NoFaultMeansNoFault) {
  EventSpec e;  // fault phasors equal prefault phasors
  const AnalysisResult r = analyze(build_record(e), base_settings());
  EXPECT_FALSE(r.fault_detected);
  EXPECT_EQ(fact(r, "fault.type").kind, FactKind::Missing);
}

TEST(Analyzer, CurrentsOnlyRecord) {
  EventSpec e = fault_event("CG", 8.0);
  e.with_voltage = false;
  e.t_return = 0.3;
  const AnalysisResult r = analyze(build_record(e), base_settings());
  EXPECT_EQ(to_string(r.fault_type), "CG");
  EXPECT_EQ(r.direction, Direction::Unknown);
  EXPECT_EQ(fact(r, "fault.direction").kind, FactKind::Missing);
  ASSERT_TRUE(r.inception_s.has_value());
  EXPECT_NEAR(*r.inception_s, e.t_fault, 3.0 / 1920.0);
}

TEST(Analyzer, MissingTripChannelIsReportedNotGuessed) {
  EventSpec e = fault_event("AB", 8.0);
  e.t_return = 0.3;
  Record rec = build_record(e);
  rec.cfg.digitals.clear();
  rec.digital.clear();
  const AnalysisResult r = analyze(rec, base_settings());
  const Fact& op = fact(r, "relay.operate_ms");
  EXPECT_EQ(op.kind, FactKind::Missing);
  EXPECT_NE(op.reason.find("no trip output channel"), std::string::npos);
}
