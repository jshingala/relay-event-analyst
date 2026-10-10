#include "rea/dsp.hpp"

#include <cmath>
#include <limits>
#include <stdexcept>

namespace rea {
namespace {
constexpr double kPi = 3.14159265358979323846;
}

const char* to_string(FilterType f) {
  return f == FilterType::Dft ? "dft" : "cosine";
}

double deg(double r) { return r * 180.0 / kPi; }
double rad(double d) { return d * kPi / 180.0; }

int filter_latency_samples(int n, FilterType f) {
  return f == FilterType::Dft ? n - 1 : n - 1 + n / 4;
}

std::vector<cplx> estimate_phasors(const std::vector<double>& x, int n, FilterType f) {
  if (n < 4) throw std::invalid_argument("samples per cycle must be >= 4");
  if (f == FilterType::Cosine && n % 4 != 0) {
    throw std::invalid_argument("cosine filter needs samples per cycle divisible by 4");
  }
  const double nan = std::numeric_limits<double>::quiet_NaN();
  const std::size_t k_total = x.size();
  std::vector<cplx> out(k_total, cplx(nan, nan));
  if (k_total < static_cast<std::size_t>(n)) return out;

  std::vector<double> cos_t(static_cast<std::size_t>(n));
  std::vector<double> sin_t(static_cast<std::size_t>(n));
  for (int i = 0; i < n; ++i) {
    cos_t[static_cast<std::size_t>(i)] = std::cos(2.0 * kPi * i / n);
    sin_t[static_cast<std::size_t>(i)] = std::sin(2.0 * kPi * i / n);
  }
  const auto un = static_cast<std::size_t>(n);

  if (f == FilterType::Dft) {
    // X(k) = sqrt(2)/N * sum_{m=k-N+1}^{k} x[m] * exp(-j*2*pi*m/N)  (absolute index m)
    const double scale = std::sqrt(2.0) / n;
    for (std::size_t k = un - 1; k < k_total; ++k) {
      double re = 0.0;
      double im = 0.0;
      for (std::size_t i = 0; i < un; ++i) {
        const std::size_t m = k + 1 - un + i;
        const std::size_t idx = m % un;
        re += x[m] * cos_t[idx];
        im -= x[m] * sin_t[idx];
      }
      out[k] = cplx(re * scale, im * scale);
    }
    return out;
  }

  // Cosine filter: Xc(k) = 2/N * sum_{i=0}^{N-1} x[k-N+1+i] * cos(2*pi*i/N) = Xpk*cos(2*pi*(k+1)/N + phi).
  std::vector<double> xc(k_total, nan);
  const double c_scale = 2.0 / n;
  for (std::size_t k = un - 1; k < k_total; ++k) {
    double acc = 0.0;
    for (std::size_t i = 0; i < un; ++i) acc += x[k + 1 - un + i] * cos_t[i];
    xc[k] = acc * c_scale;
  }
  // Quarter-cycle-old output is the quadrature component: Xc(k-N/4) = Xpk*sin(2*pi*(k+1)/N + phi).
  const std::size_t q = un / 4;
  const double r_scale = 1.0 / std::sqrt(2.0);
  for (std::size_t k = un - 1 + q; k < k_total; ++k) {
    const cplx rotating(xc[k], xc[k - q]);
    const std::size_t idx = (k + 1) % un;
    const cplx derotate(cos_t[idx], -sin_t[idx]);  // exp(-j*2*pi*(k+1)/N)
    out[k] = rotating * derotate * r_scale;
  }
  return out;
}

double instantaneous_from_phasor(cplx phasor, long long m, int n) {
  const double ang = 2.0 * kPi * static_cast<double>(m % n) / n;
  return std::sqrt(2.0) * (phasor * cplx(std::cos(ang), std::sin(ang))).real();
}

Sequence to_sequence(cplx a_ph, cplx b_ph, cplx c_ph, bool abc) {
  const cplx a = std::polar(1.0, 2.0 * kPi / 3.0);
  const cplx a2 = a * a;
  if (!abc) std::swap(b_ph, c_ph);
  Sequence s;
  s.zero = (a_ph + b_ph + c_ph) / 3.0;
  s.pos = (a_ph + a * b_ph + a2 * c_ph) / 3.0;
  s.neg = (a_ph + a2 * b_ph + a * c_ph) / 3.0;
  return s;
}

}  // namespace rea
