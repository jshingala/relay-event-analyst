#include <gtest/gtest.h>

#include <cmath>

#include "rea/dsp.hpp"
#include "test_support.hpp"

using rea::cplx;
using rea::FilterType;
using rea::test::kPi;

namespace {

std::vector<double> sinusoid(int n, int cycles, double rms, double ang_deg, double dc = 0.0,
                             double tau_samples = 0.0) {
  std::vector<double> x(static_cast<std::size_t>(n * cycles));
  for (std::size_t k = 0; k < x.size(); ++k) {
    x[k] = std::sqrt(2.0) * rms * std::cos(2.0 * kPi * static_cast<double>(k) / n + ang_deg * kPi / 180.0);
    if (dc != 0.0) x[k] += dc * std::exp(-static_cast<double>(k) / tau_samples);
  }
  return x;
}

}  // namespace

class FilterTest : public ::testing::TestWithParam<FilterType> {};

TEST_P(FilterTest, SteadySinusoidGivesExactStationaryPhasor) {
  const int n = 16;
  const auto x = sinusoid(n, 6, 7.5, 33.0);
  const auto p = rea::estimate_phasors(x, n, GetParam());
  const int lat = rea::filter_latency_samples(n, GetParam());
  EXPECT_TRUE(std::isnan(p[static_cast<std::size_t>(lat - 1)].real()));
  for (std::size_t k = static_cast<std::size_t>(lat); k < x.size(); ++k) {
    EXPECT_NEAR(std::abs(p[k]), 7.5, 1e-9) << "k=" << k;
    EXPECT_NEAR(rea::deg(std::arg(p[k])), 33.0, 1e-7) << "k=" << k;
  }
}

TEST_P(FilterTest, PhasorRebuildsTheWaveform) {
  const int n = 32;
  const auto x = sinusoid(n, 4, 3.0, -71.0);
  const auto p = rea::estimate_phasors(x, n, GetParam());
  const auto k = static_cast<std::size_t>(3 * n + 5);
  EXPECT_NEAR(rea::instantaneous_from_phasor(p[k], static_cast<long long>(k), n), x[k], 1e-9);
}

INSTANTIATE_TEST_SUITE_P(BothFilters, FilterTest, ::testing::Values(FilterType::Dft, FilterType::Cosine));

TEST(Dsp, CosineFilterRejectsDecayingDcBetterThanDft) {
  // Fully offset fault current: DC equal to the AC peak, decaying with tau = 1.5 cycles.
  const int n = 32;
  const double rms = 10.0;
  const auto x = sinusoid(n, 8, rms, 0.0, -std::sqrt(2.0) * rms, 1.5 * n);
  const auto dft = rea::estimate_phasors(x, n, FilterType::Dft);
  const auto cosf = rea::estimate_phasors(x, n, FilterType::Cosine);
  double worst_dft = 0.0;
  double worst_cos = 0.0;
  for (std::size_t k = static_cast<std::size_t>(rea::filter_latency_samples(n, FilterType::Cosine)); k < x.size(); ++k) {
    worst_dft = std::max(worst_dft, std::fabs(std::abs(dft[k]) - rms) / rms);
    worst_cos = std::max(worst_cos, std::fabs(std::abs(cosf[k]) - rms) / rms);
  }
  EXPECT_LT(worst_cos, worst_dft);
  EXPECT_LT(worst_cos, 0.10);
  EXPECT_GT(worst_dft, 0.10);
}

TEST(Dsp, CosineFilterRequiresQuarterCycleMultiple) {
  std::vector<double> x(100, 0.0);
  EXPECT_THROW(rea::estimate_phasors(x, 10, FilterType::Cosine), std::invalid_argument);
  EXPECT_NO_THROW(rea::estimate_phasors(x, 10, FilterType::Dft));
}

TEST(Dsp, SequenceComponents) {
  using rea::test::balanced;
  const auto pos = balanced(5.0, 10.0);
  const auto s = rea::to_sequence(pos.a, pos.b, pos.c);
  EXPECT_NEAR(std::abs(s.pos), 5.0, 1e-12);
  EXPECT_NEAR(std::abs(s.neg), 0.0, 1e-12);
  EXPECT_NEAR(std::abs(s.zero), 0.0, 1e-12);
  // Negative-sequence set (A, C, B order) shows up as I2 only.
  const auto n2 = rea::to_sequence(pos.a, pos.c, pos.b);
  EXPECT_NEAR(std::abs(n2.neg), 5.0, 1e-12);
  EXPECT_NEAR(std::abs(n2.pos), 0.0, 1e-12);
  // A single-phase current splits equally into I0 = I1 = I2.
  const auto slg = rea::to_sequence(cplx(3.0, 0.0), 0.0, 0.0);
  EXPECT_NEAR(std::abs(slg.zero - slg.pos), 0.0, 1e-12);
  EXPECT_NEAR(std::abs(slg.pos - slg.neg), 0.0, 1e-12);
  EXPECT_NEAR(std::abs(slg.zero), 1.0, 1e-12);
}
