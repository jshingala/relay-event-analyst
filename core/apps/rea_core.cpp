// rea-core: command-line front end of the physics core.
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <optional>
#include <string>
#include <vector>

#include "rea/analyzer.hpp"
#include "rea/comtrade.hpp"
#include "rea/curves.hpp"
#include "rea/dsp.hpp"
#include "rea/facts.hpp"
#include "rea/settings.hpp"

namespace {

constexpr const char* kVersion = "0.1.0";

void usage() {
  std::cerr << "rea-core " << kVersion << " - offline protective-relay event analysis (physics core)\n\n"
            << "Usage:\n"
            << "  rea-core analyze <event.cfg> [--settings FILE] [--filter cosine|dft] [--compact]\n"
            << "  rea-core info <event.cfg>\n"
            << "  rea-core phasors <event.cfg> [--filter cosine|dft] [--every N]\n"
            << "  rea-core curve <U1..U5|C1..C5> <time-dial> <multiple-of-pickup>\n"
            << "  rea-core --version\n";
}

struct Args {
  std::vector<std::string> positional;
  std::optional<std::string> settings;
  rea::FilterType filter = rea::FilterType::Cosine;
  bool compact = false;
  int every = 1;
};

Args parse_args(int argc, char** argv, int start) {
  Args a;
  for (int i = start; i < argc; ++i) {
    const std::string arg = argv[i];
    auto value = [&]() -> std::string {
      if (i + 1 >= argc) throw std::invalid_argument(arg + " needs a value");
      return argv[++i];
    };
    if (arg == "--settings") {
      a.settings = value();
    } else if (arg == "--filter") {
      const std::string f = value();
      if (f == "dft") a.filter = rea::FilterType::Dft;
      else if (f == "cosine") a.filter = rea::FilterType::Cosine;
      else throw std::invalid_argument("--filter must be cosine or dft");
    } else if (arg == "--compact") {
      a.compact = true;
    } else if (arg == "--every") {
      a.every = std::max(1, std::stoi(value()));
    } else if (!arg.empty() && arg[0] == '-') {
      throw std::invalid_argument("unknown option " + arg);
    } else {
      a.positional.push_back(arg);
    }
  }
  return a;
}

double ms_since(std::chrono::steady_clock::time_point t0) {
  return std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count();
}

int cmd_analyze(const Args& a) {
  if (a.positional.size() != 1) throw std::invalid_argument("analyze needs exactly one .cfg path");
  const auto t0 = std::chrono::steady_clock::now();
  const rea::Record rec = rea::load_record(a.positional[0]);
  const double parse_ms = ms_since(t0);
  const rea::Settings settings = a.settings ? rea::Settings::load(*a.settings) : rea::Settings{};
  rea::AnalyzerOptions opt;
  opt.filter = a.filter;
  rea::AnalysisResult r = rea::analyze(rec, settings, opt);
  r.facts.meta("parse_ms", parse_ms);
  std::cout << r.facts.to_json(!a.compact);
  return 0;
}

int cmd_info(const Args& a) {
  if (a.positional.size() != 1) throw std::invalid_argument("info needs exactly one .cfg path");
  const rea::Record rec = rea::load_record(a.positional[0]);
  const auto& c = rec.cfg;
  std::cout << "station: " << c.station << "\ndevice: " << c.device << "\nrevision: " << c.rev_year
            << "\nformat: " << rea::to_string(c.format) << "\nline frequency: " << c.line_freq_hz
            << " Hz\nsample rate: " << rec.sample_rate_hz() << " Hz\nsamples: " << rec.num_samples()
            << "\nstart: " << c.start.text << "\ntrigger: " << c.trigger.text << "\nanalog channels:\n";
  for (const auto& ch : c.analogs) {
    std::cout << "  " << ch.index << " " << ch.id << " phase=" << ch.phase << " unit=" << ch.unit
              << " ratio=" << ch.primary << "/" << ch.secondary << " " << ch.ps << "\n";
  }
  std::cout << "digital channels:\n";
  for (const auto& ch : c.digitals) std::cout << "  " << ch.index << " " << ch.id << "\n";
  std::vector<std::string> notes;
  const auto m = rea::map_channels(rec, rea::Settings{}, &notes);
  std::cout << "mapped: IA=" << m.ia << " IB=" << m.ib << " IC=" << m.ic << " VA=" << m.va << " VB=" << m.vb
            << " VC=" << m.vc << " TRIP=" << m.trip << " 52A=" << m.breaker << "\n";
  for (const auto& w : rec.warnings) std::cout << "warning: " << w << "\n";
  return 0;
}

int cmd_phasors(const Args& a) {
  if (a.positional.size() != 1) throw std::invalid_argument("phasors needs exactly one .cfg path");
  const rea::Record rec = rea::load_record(a.positional[0]);
  const auto m = rea::map_channels(rec, rea::Settings{});
  const int n = static_cast<int>(std::lround(rec.sample_rate_hz() / rec.cfg.line_freq_hz));
  std::vector<int> idx{m.ia, m.ib, m.ic, m.va, m.vb, m.vc};
  const char* names[] = {"IA", "IB", "IC", "VA", "VB", "VC"};
  std::vector<std::vector<rea::cplx>> ph;
  std::cout << "t_s";
  for (std::size_t i = 0; i < idx.size(); ++i) {
    if (idx[i] < 0) continue;
    ph.push_back(rea::estimate_phasors(rec.analog[static_cast<std::size_t>(idx[i])], n, a.filter));
    std::cout << "," << names[i] << "_mag," << names[i] << "_deg";
  }
  std::cout << "\n";
  for (std::size_t k = 0; k < rec.num_samples(); k += static_cast<std::size_t>(a.every)) {
    std::cout << rec.time_s[k];
    for (const auto& p : ph) std::cout << "," << std::abs(p[k]) << "," << rea::deg(std::arg(p[k]));
    std::cout << "\n";
  }
  return 0;
}

int cmd_curve(const Args& a) {
  if (a.positional.size() != 3) throw std::invalid_argument("curve needs <curve> <time-dial> <multiple>");
  const auto c = rea::parse_curve(a.positional[0]);
  if (!c) throw std::invalid_argument("unknown curve " + a.positional[0]);
  const double td = std::stod(a.positional[1]);
  const double m = std::stod(a.positional[2]);
  const double t = rea::operate_time_s(*c, td, m);
  std::cout << rea::curve_name(*c) << " (" << rea::curve_def(*c).description << ") TD=" << td << " M=" << m
            << ": " << (std::isfinite(t) ? rea::format_fixed(t, 4) + " s" : std::string("no operation")) << "\n";
  return 0;
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 2) {
    usage();
    return 1;
  }
  const std::string cmd = argv[1];
  if (cmd == "--version" || cmd == "-V") {
    std::cout << "rea-core " << kVersion << "\n";
    return 0;
  }
  if (cmd == "--help" || cmd == "-h") {
    usage();
    return 0;
  }
  try {
    const Args a = parse_args(argc, argv, 2);
    if (cmd == "analyze") return cmd_analyze(a);
    if (cmd == "info") return cmd_info(a);
    if (cmd == "phasors") return cmd_phasors(a);
    if (cmd == "curve") return cmd_curve(a);
    usage();
    return 1;
  } catch (const std::exception& e) {
    if (cmd == "analyze") {
      std::cout << "{\"schema\": \"rea.facts/1\", \"error\": \"" << rea::json_escape(e.what()) << "\"}\n";
    }
    std::cerr << "rea-core: " << e.what() << "\n";
    return 2;
  }
}
