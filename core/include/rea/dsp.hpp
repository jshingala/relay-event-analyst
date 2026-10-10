// Phasor estimation and symmetrical components.
//
// Both filters return fixed-reference RMS phasors: for a steady sinusoid
// x[m] = Xpk * cos(2*pi*m/N + phi) at nominal frequency the estimate is
// (Xpk / sqrt(2)) * exp(j*phi) at every sample, so phasors from different
// channels and different instants can be compared and subtracted directly.
#pragma once

#include <complex>
#include <vector>

namespace rea {

using cplx = std::complex<double>;

enum class FilterType {
  Dft,     // full-cycle Fourier filter (window: N samples)
  Cosine,  // full-cycle cosine filter, quadrature from the output N/4 samples earlier
           // (window: N + N/4 samples); rejects decaying DC offset better than the DFT
};

const char* to_string(FilterType f);

// Index of the first sample with a valid phasor (window fully inside the record).
int filter_latency_samples(int samples_per_cycle, FilterType f);

// Phasor for every sample k, computed from the window that ends at k.
// Entries before filter_latency_samples() are NaN. Requires samples_per_cycle >= 4,
// and samples_per_cycle % 4 == 0 for the cosine filter.
std::vector<cplx> estimate_phasors(const std::vector<double>& x, int samples_per_cycle,
                                   FilterType f);

// Rebuild the instantaneous waveform value at sample m from a fixed-reference phasor.
double instantaneous_from_phasor(cplx phasor, long long m, int samples_per_cycle);

struct Sequence {
  cplx zero;  // I0 (or V0)
  cplx pos;   // I1
  cplx neg;   // I2
};

// Symmetrical components with operator a = 1/120 deg. For ACB rotation pass abc=false.
Sequence to_sequence(cplx a_phase, cplx b_phase, cplx c_phase, bool abc = true);

double deg(double rad);
double rad(double deg);

}  // namespace rea
