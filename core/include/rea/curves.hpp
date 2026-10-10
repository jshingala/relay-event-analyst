// Inverse-time overcurrent curves (IEEE C37.112 form) and an integrating element.
//
// Operate time for a constant multiple of pickup M > 1:
//     tp = TD * (A + B / (M^p - 1))
// Electromechanical reset emulation for M < 1:
//     tr = TD * R / (1 - M^2)        (C4: TD * R / (1 - M))
//
// Coefficients are the SEL U.S. (U1-U5) and IEC (C1-C5) curves as published in
// SEL's "PowerSystemProtection IEC 61131 Library for ACSELERATOR RTAC Projects"
// (Tables 1 and 2). See docs/physics.md.
//
// For time-varying current the element integrates 1/tp(M(t)) and operates when
// the integral reaches 1, which is the integral form IEEE C37.112 defines for
// microprocessor relays.
#pragma once

#include <optional>
#include <string>

namespace rea {

enum class Curve { U1, U2, U3, U4, U5, C1, C2, C3, C4, C5 };

struct CurveDef {
  Curve id;
  const char* name;
  const char* description;
  double A;        // constant term
  double B;        // numerator
  double p;        // exponent of M
  double R;        // reset numerator
  bool reset_m2;   // reset uses (1 - M^2); false means (1 - M)
};

const CurveDef& curve_def(Curve c);
std::optional<Curve> parse_curve(const std::string& name);
std::string curve_name(Curve c);

// Seconds to operate at constant multiple M. +infinity when M <= 1.
double operate_time_s(Curve c, double td, double m);
// Seconds to fully reset at constant multiple M < 1 (EM reset). +infinity when M >= 1.
double reset_time_s(Curve c, double td, double m);

class InverseTimeElement {
 public:
  InverseTimeElement(Curve curve, double pickup, double td, bool em_reset);

  // Advance by dt seconds with a measured magnitude in the same units as pickup.
  // Returns the fraction of dt (0..1] at which the element operated, or nullopt.
  std::optional<double> step(double magnitude, double dt);

  double travel() const { return travel_; }
  bool operated() const { return operated_; }
  double multiple(double magnitude) const { return magnitude / pickup_; }
  Curve curve() const { return curve_; }
  double pickup() const { return pickup_; }
  double time_dial() const { return td_; }

 private:
  Curve curve_;
  double pickup_;
  double td_;
  bool em_reset_;
  double travel_ = 0.0;
  bool operated_ = false;
};

}  // namespace rea
