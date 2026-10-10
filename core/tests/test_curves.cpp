#include <gtest/gtest.h>

#include <cmath>

#include "rea/curves.hpp"

using rea::Curve;

TEST(Curves, OperateTimesMatchPublishedEquations) {
  // U3 Very Inverse: TD * (0.0963 + 3.88 / (M^2 - 1))
  EXPECT_NEAR(rea::operate_time_s(Curve::U3, 2.0, 5.0), 2.0 * (0.0963 + 3.88 / 24.0), 1e-12);
  // U4 Extremely Inverse: TD * (0.0352 + 5.67 / (M^2 - 1))
  EXPECT_NEAR(rea::operate_time_s(Curve::U4, 1.0, 10.0), 0.0352 + 5.67 / 99.0, 1e-12);
  // U1 Moderately Inverse: TD * (0.0226 + 0.0104 / (M^0.02 - 1))
  EXPECT_NEAR(rea::operate_time_s(Curve::U1, 3.0, 4.0), 3.0 * (0.0226 + 0.0104 / (std::pow(4.0, 0.02) - 1.0)), 1e-12);
  // U2 and U5
  EXPECT_NEAR(rea::operate_time_s(Curve::U2, 1.0, 3.0), 0.180 + 5.95 / 8.0, 1e-12);
  EXPECT_NEAR(rea::operate_time_s(Curve::U5, 1.0, 2.0), 0.00262 + 0.00342 / (std::pow(2.0, 0.02) - 1.0), 1e-12);
  // IEC C1 Standard Inverse: TD * 0.14 / (M^0.02 - 1)
  EXPECT_NEAR(rea::operate_time_s(Curve::C1, 0.5, 10.0), 0.5 * 0.14 / (std::pow(10.0, 0.02) - 1.0), 1e-12);
  EXPECT_NEAR(rea::operate_time_s(Curve::C2, 0.2, 4.0), 0.2 * 13.5 / 3.0, 1e-12);
  EXPECT_NEAR(rea::operate_time_s(Curve::C3, 0.1, 3.0), 0.1 * 80.0 / 8.0, 1e-12);
  EXPECT_NEAR(rea::operate_time_s(Curve::C4, 0.1, 3.0), 0.1 * 120.0 / 2.0, 1e-12);
  EXPECT_NEAR(rea::operate_time_s(Curve::C5, 0.1, 3.0), 0.1 * 0.05 / (std::pow(3.0, 0.04) - 1.0), 1e-12);
}

TEST(Curves, NoOperationAtOrBelowPickup) {
  EXPECT_TRUE(std::isinf(rea::operate_time_s(Curve::U3, 1.0, 1.0)));
  EXPECT_TRUE(std::isinf(rea::operate_time_s(Curve::U3, 1.0, 0.5)));
}

TEST(Curves, ResetEquations) {
  EXPECT_NEAR(rea::reset_time_s(Curve::U3, 2.0, 0.5), 2.0 * 3.88 / 0.75, 1e-12);
  EXPECT_NEAR(rea::reset_time_s(Curve::C4, 1.0, 0.5), 120.0 / 0.5, 1e-12);
  EXPECT_TRUE(std::isinf(rea::reset_time_s(Curve::U3, 1.0, 1.2)));
}

TEST(Curves, ParseIsCaseInsensitive) {
  EXPECT_EQ(rea::parse_curve("u3"), Curve::U3);
  EXPECT_EQ(rea::parse_curve(" C1 "), Curve::C1);
  EXPECT_FALSE(rea::parse_curve("U9").has_value());
}

TEST(InverseTimeElement, ConstantCurrentOperatesAtCurveTime) {
  const double dt = 1.0 / 1920.0;
  rea::InverseTimeElement el(Curve::U3, 6.0, 2.0, false);
  const double expected = rea::operate_time_s(Curve::U3, 2.0, 30.0 / 6.0);
  double t = 0.0;
  for (int k = 0; k < 100000; ++k) {
    if (auto frac = el.step(30.0, dt)) {
      t += *frac * dt;
      break;
    }
    t += dt;
  }
  EXPECT_TRUE(el.operated());
  EXPECT_NEAR(t, expected, 1e-9);
}

TEST(InverseTimeElement, VaryingCurrentFollowsTheIntegral) {
  // Half the travel at M=2, then the rest at M=8: total = tp(2)/2 + tp(8)/2.
  const double dt = 1e-4;
  rea::InverseTimeElement el(Curve::U2, 1.0, 1.0, false);
  const double tp2 = rea::operate_time_s(Curve::U2, 1.0, 2.0);
  const double tp8 = rea::operate_time_s(Curve::U2, 1.0, 8.0);
  double t = 0.0;
  while (el.travel() < 0.5 - 1e-12) {
    el.step(2.0, dt);
    t += dt;
  }
  double t_trip = -1.0;
  for (int k = 0; k < 1000000 && t_trip < 0.0; ++k) {
    if (auto frac = el.step(8.0, dt)) t_trip = t + *frac * dt;
    t += dt;
  }
  EXPECT_NEAR(t_trip, 0.5 * tp2 + 0.5 * tp8, 2 * dt);
}

TEST(InverseTimeElement, ResetBehaviour) {
  rea::InverseTimeElement em(Curve::U3, 1.0, 1.0, true);
  rea::InverseTimeElement fast(Curve::U3, 1.0, 1.0, false);
  for (int k = 0; k < 100; ++k) {
    em.step(3.0, 1e-3);
    fast.step(3.0, 1e-3);
  }
  const double travel = em.travel();
  ASSERT_GT(travel, 0.0);
  em.step(0.0, 0.1);  // EM reset at M=0: full reset takes TD * 3.88 s
  EXPECT_NEAR(em.travel(), travel - 0.1 / 3.88, 1e-12);
  fast.step(0.0, 1e-3);
  EXPECT_EQ(fast.travel(), 0.0);
}
