#include "rea/curves.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace rea {
namespace {

constexpr double kInf = std::numeric_limits<double>::infinity();

// SEL RTAC PowerSystemProtection library, Table 1 (IEC) and Table 2 (U.S.).
constexpr std::array<CurveDef, 10> kCurves{{
    {Curve::U1, "U1", "U.S. Moderately Inverse", 0.0226, 0.0104, 0.02, 1.08, true},
    {Curve::U2, "U2", "U.S. Inverse", 0.180, 5.95, 2.0, 5.95, true},
    {Curve::U3, "U3", "U.S. Very Inverse", 0.0963, 3.88, 2.0, 3.88, true},
    {Curve::U4, "U4", "U.S. Extremely Inverse", 0.0352, 5.67, 2.0, 5.67, true},
    {Curve::U5, "U5", "U.S. Short-Time Inverse", 0.00262, 0.00342, 0.02, 0.323, true},
    {Curve::C1, "C1", "IEC Standard Inverse", 0.0, 0.14, 0.02, 13.5, true},
    {Curve::C2, "C2", "IEC Very Inverse", 0.0, 13.5, 1.0, 47.3, true},
    {Curve::C3, "C3", "IEC Extremely Inverse", 0.0, 80.0, 2.0, 80.0, true},
    {Curve::C4, "C4", "IEC Long-Time Inverse", 0.0, 120.0, 1.0, 120.0, false},
    {Curve::C5, "C5", "IEC Short-Time Inverse", 0.0, 0.05, 0.04, 4.85, true},
}};

}  // namespace

const CurveDef& curve_def(Curve c) {
  for (const auto& d : kCurves) {
    if (d.id == c) return d;
  }
  throw std::invalid_argument("unknown curve");
}

std::optional<Curve> parse_curve(const std::string& name) {
  std::string u;
  for (char ch : name) {
    if (!std::isspace(static_cast<unsigned char>(ch))) {
      u.push_back(static_cast<char>(std::toupper(static_cast<unsigned char>(ch))));
    }
  }
  for (const auto& d : kCurves) {
    if (u == d.name) return d.id;
  }
  return std::nullopt;
}

std::string curve_name(Curve c) { return curve_def(c).name; }

double operate_time_s(Curve c, double td, double m) {
  if (!(m > 1.0) || !(td > 0.0)) return kInf;
  const auto& d = curve_def(c);
  const double denom = std::pow(m, d.p) - 1.0;
  if (!(denom > 0.0)) return kInf;
  return td * (d.A + d.B / denom);
}

double reset_time_s(Curve c, double td, double m) {
  if (!(m < 1.0) || !(td > 0.0)) return kInf;
  const auto& d = curve_def(c);
  const double mm = std::max(m, 0.0);
  const double denom = d.reset_m2 ? (1.0 - mm * mm) : (1.0 - mm);
  return td * d.R / denom;
}

InverseTimeElement::InverseTimeElement(Curve curve, double pickup, double td, bool em_reset)
    : curve_(curve), pickup_(pickup), td_(td), em_reset_(em_reset) {
  if (!(pickup > 0.0)) throw std::invalid_argument("pickup must be > 0");
  if (!(td > 0.0)) throw std::invalid_argument("time dial must be > 0");
}

std::optional<double> InverseTimeElement::step(double magnitude, double dt) {
  if (operated_ || !(dt > 0.0) || std::isnan(magnitude)) return std::nullopt;
  const double m = magnitude / pickup_;
  if (m > 1.0) {
    const double tp = operate_time_s(curve_, td_, m);
    if (!std::isfinite(tp) || tp <= 0.0) return std::nullopt;
    const double increment = dt / tp;
    if (travel_ + increment >= 1.0) {
      const double fraction = (1.0 - travel_) / increment;  // portion of dt used
      travel_ = 1.0;
      operated_ = true;
      return std::clamp(fraction, 0.0, 1.0);
    }
    travel_ += increment;
  } else if (m < 1.0) {
    if (em_reset_) {
      const double tr = reset_time_s(curve_, td_, m);
      if (std::isfinite(tr) && tr > 0.0) travel_ = std::max(0.0, travel_ - dt / tr);
    } else {
      travel_ = 0.0;  // simplification: instantaneous reset when 51xRS = N
    }
  }
  return std::nullopt;
}

}  // namespace rea
