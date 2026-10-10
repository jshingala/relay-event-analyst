#include "rea/comtrade.hpp"

#include <algorithm>
#include <cctype>
#include <cerrno>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <limits>
#include <sstream>
#include <string_view>

namespace rea {
namespace {

constexpr double kNaN = std::numeric_limits<double>::quiet_NaN();

std::string trim(std::string_view s) {
  std::size_t b = 0;
  std::size_t e = s.size();
  while (b < e && std::isspace(static_cast<unsigned char>(s[b]))) ++b;
  while (e > b && std::isspace(static_cast<unsigned char>(s[e - 1]))) --e;
  return std::string(s.substr(b, e - b));
}

std::string upper(std::string s) {
  for (auto& c : s) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
  return s;
}

std::vector<std::string> split(const std::string& s, char delim) {
  std::vector<std::string> out;
  std::size_t start = 0;
  for (;;) {
    const std::size_t pos = s.find(delim, start);
    if (pos == std::string::npos) {
      out.push_back(s.substr(start));
      break;
    }
    out.push_back(s.substr(start, pos - start));
    start = pos + 1;
  }
  return out;
}

std::vector<std::string> split_lines(const std::string& text) {
  std::vector<std::string> lines;
  std::size_t i = 0;
  // Skip a UTF-8 byte order mark if present.
  if (text.size() >= 3 && static_cast<unsigned char>(text[0]) == 0xEF &&
      static_cast<unsigned char>(text[1]) == 0xBB && static_cast<unsigned char>(text[2]) == 0xBF) {
    i = 3;
  }
  std::string cur;
  for (; i < text.size(); ++i) {
    const char c = text[i];
    if (c == '\r') {
      lines.push_back(cur);
      cur.clear();
      if (i + 1 < text.size() && text[i + 1] == '\n') ++i;
    } else if (c == '\n') {
      lines.push_back(cur);
      cur.clear();
    } else {
      cur.push_back(c);
    }
  }
  if (!cur.empty()) lines.push_back(cur);
  return lines;
}

bool parse_number(const std::string& field, double& out) {
  const std::string t = trim(field);
  if (t.empty()) return false;
  char* end = nullptr;
  errno = 0;
  const double v = std::strtod(t.c_str(), &end);
  if (end == t.c_str() || errno == ERANGE) return false;
  while (*end != '\0' && std::isspace(static_cast<unsigned char>(*end))) ++end;
  if (*end != '\0') return false;
  out = v;
  return true;
}

double require_number(const std::string& field, const char* what, std::size_t line_no) {
  double v = 0.0;
  if (!parse_number(field, v)) {
    std::ostringstream msg;
    msg << "CFG line " << line_no << ": invalid " << what << " '" << trim(field) << "'";
    throw ComtradeError(msg.str());
  }
  return v;
}

double number_or(const std::string& field, double fallback) {
  double v = 0.0;
  return parse_number(field, v) ? v : fallback;
}

int leading_int(const std::string& field) {
  const std::string t = trim(field);
  int v = 0;
  bool any = false;
  for (char c : t) {
    if (!std::isdigit(static_cast<unsigned char>(c))) break;
    v = v * 10 + (c - '0');
    any = true;
  }
  return any ? v : -1;
}

long long days_from_civil(long long y, unsigned m, unsigned d) {
  y -= m <= 2 ? 1 : 0;
  const long long era = (y >= 0 ? y : y - 399) / 400;
  const auto yoe = static_cast<unsigned>(y - era * 400);
  const unsigned mp = m > 2 ? m - 3 : m + 9;
  const unsigned doy = (153 * mp + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  return era * 146097 + static_cast<long long>(doe) - 719468;
}

Timestamp parse_timestamp(const std::string& raw, int rev_year) {
  Timestamp ts;
  ts.text = trim(raw);
  const auto comma = ts.text.find(',');
  if (comma == std::string::npos) return ts;
  const auto d = split(trim(ts.text.substr(0, comma)), '/');
  const auto t = split(trim(ts.text.substr(comma + 1)), ':');
  if (d.size() != 3 || t.size() != 3) return ts;
  double p0 = 0, p1 = 0, p2 = 0, hh = 0, mm = 0, ss = 0;
  if (!parse_number(d[0], p0) || !parse_number(d[1], p1) || !parse_number(d[2], p2) ||
      !parse_number(t[0], hh) || !parse_number(t[1], mm) || !parse_number(t[2], ss)) {
    return ts;
  }
  if (rev_year >= 1999) {
    ts.day = static_cast<int>(p0);
    ts.month = static_cast<int>(p1);
    ts.year = static_cast<int>(p2);
  } else {
    // 1991 uses mm/dd/yy.
    ts.month = static_cast<int>(p0);
    ts.day = static_cast<int>(p1);
    const int yy = static_cast<int>(p2);
    ts.year = yy < 100 ? (yy < 50 ? 2000 + yy : 1900 + yy) : yy;
  }
  ts.hour = static_cast<int>(hh);
  ts.minute = static_cast<int>(mm);
  ts.second = ss;
  ts.valid = ts.month >= 1 && ts.month <= 12 && ts.day >= 1 && ts.day <= 31 && ts.hour >= 0 &&
             ts.hour < 24 && ts.minute >= 0 && ts.minute < 60 && ts.second >= 0.0 &&
             ts.second < 61.0;
  return ts;
}

std::uint16_t le16(const unsigned char* p) {
  return static_cast<std::uint16_t>(p[0] | (p[1] << 8));
}

std::uint32_t le32(const unsigned char* p) {
  return static_cast<std::uint32_t>(p[0]) | (static_cast<std::uint32_t>(p[1]) << 8) |
         (static_cast<std::uint32_t>(p[2]) << 16) | (static_cast<std::uint32_t>(p[3]) << 24);
}

}  // namespace

std::string to_string(DataFormat f) {
  switch (f) {
    case DataFormat::Ascii: return "ASCII";
    case DataFormat::Binary: return "BINARY";
    case DataFormat::Binary32: return "BINARY32";
    case DataFormat::Float32: return "FLOAT32";
  }
  return "UNKNOWN";
}

double Timestamp::epoch_seconds() const {
  const long long days =
      days_from_civil(year, static_cast<unsigned>(month), static_cast<unsigned>(day));
  return static_cast<double>(days) * 86400.0 + hour * 3600.0 + minute * 60.0 + second;
}

long long Config::declared_samples() const {
  return rates.empty() ? 0 : rates.back().end_sample;
}

double Record::sample_rate_hz() const {
  if (!cfg.rates.empty() && cfg.rates.front().rate_hz > 0.0) return cfg.rates.front().rate_hz;
  if (time_s.size() < 2) return 0.0;
  std::vector<double> dt;
  dt.reserve(time_s.size() - 1);
  for (std::size_t k = 1; k < time_s.size(); ++k) dt.push_back(time_s[k] - time_s[k - 1]);
  std::nth_element(dt.begin(), dt.begin() + static_cast<long>(dt.size() / 2), dt.end());
  const double med = dt[dt.size() / 2];
  return med > 0.0 ? 1.0 / med : 0.0;
}

bool Record::trigger_offset_s(double& out) const {
  if (!cfg.start.valid || !cfg.trigger.valid) return false;
  // Difference field by field: epoch seconds (~1.8e9) would lose sub-microsecond precision.
  const auto& a = cfg.start;
  const auto& b = cfg.trigger;
  const long long days = days_from_civil(b.year, static_cast<unsigned>(b.month), static_cast<unsigned>(b.day)) -
                         days_from_civil(a.year, static_cast<unsigned>(a.month), static_cast<unsigned>(a.day));
  out = static_cast<double>(days) * 86400.0 + (b.hour - a.hour) * 3600.0 + (b.minute - a.minute) * 60.0 +
        (b.second - a.second);
  return true;
}

Config parse_cfg(const std::string& text) {
  const auto lines = split_lines(text);
  std::size_t ln = 0;
  auto next = [&](const char* what) -> const std::string& {
    while (ln < lines.size() && trim(lines[ln]).empty()) ++ln;
    if (ln >= lines.size()) {
      throw ComtradeError(std::string("CFG ended early: expected ") + what);
    }
    return lines[ln++];
  };

  Config cfg;
  {
    const auto f = split(next("station line"), ',');
    cfg.station = trim(f[0]);
    if (f.size() > 1) cfg.device = trim(f[1]);
    double rev = 0.0;
    cfg.rev_year = (f.size() > 2 && parse_number(f[2], rev)) ? static_cast<int>(rev) : 1991;
  }

  int n_analog = 0;
  int n_digital = 0;
  {
    const auto f = split(next("channel count line"), ',');
    if (f.size() < 3) throw ComtradeError("CFG line 2: expected 'TT,##A,##D'");
    n_analog = leading_int(f[1]);
    n_digital = leading_int(f[2]);
    const int total = leading_int(f[0]);
    if (n_analog < 0 || n_digital < 0) throw ComtradeError("CFG line 2: invalid channel counts");
    if (total >= 0 && total != n_analog + n_digital) {
      throw ComtradeError("CFG line 2: total channels does not equal analog + digital");
    }
  }

  for (int i = 0; i < n_analog; ++i) {
    const auto f = split(next("analog channel line"), ',');
    const std::size_t line_no = ln;
    if (f.size() < 10) {
      std::ostringstream msg;
      msg << "CFG line " << line_no << ": analog channel needs at least 10 fields, got " << f.size();
      throw ComtradeError(msg.str());
    }
    AnalogChannel ch;
    ch.index = leading_int(f[0]);
    ch.id = trim(f[1]);
    ch.phase = trim(f[2]);
    ch.ccbm = trim(f[3]);
    ch.unit = trim(f[4]);
    ch.a = require_number(f[5], "multiplier a", line_no);
    ch.b = number_or(f[6], 0.0);
    ch.skew_us = number_or(f[7], 0.0);
    ch.min = number_or(f[8], 0.0);
    ch.max = number_or(f[9], 0.0);
    if (f.size() >= 13) {
      ch.primary = number_or(f[10], 1.0);
      ch.secondary = number_or(f[11], 1.0);
      const std::string ps = upper(trim(f[12]));
      ch.ps = ps.empty() ? 'S' : ps[0];
      if (ch.ps != 'P' && ch.ps != 'S') {
        std::ostringstream msg;
        msg << "CFG line " << line_no << ": PS field must be P or S";
        throw ComtradeError(msg.str());
      }
      ch.has_ratio = true;
    }
    cfg.analogs.push_back(ch);
  }

  for (int i = 0; i < n_digital; ++i) {
    const auto f = split(next("digital channel line"), ',');
    const std::size_t line_no = ln;
    DigitalChannel ch;
    if (f.size() >= 5) {
      ch.index = leading_int(f[0]);
      ch.id = trim(f[1]);
      ch.phase = trim(f[2]);
      ch.ccbm = trim(f[3]);
      ch.normal_state = static_cast<int>(number_or(f[4], 0.0));
    } else if (f.size() >= 3) {
      // 1991 layout: Dn,ch_id,y
      ch.index = leading_int(f[0]);
      ch.id = trim(f[1]);
      ch.normal_state = static_cast<int>(number_or(f.back(), 0.0));
    } else {
      std::ostringstream msg;
      msg << "CFG line " << line_no << ": digital channel needs at least 3 fields";
      throw ComtradeError(msg.str());
    }
    cfg.digitals.push_back(ch);
  }

  cfg.line_freq_hz = require_number(next("line frequency"), "line frequency", ln);

  {
    const int nrates = static_cast<int>(require_number(next("nrates"), "nrates", ln));
    if (nrates < 0) throw ComtradeError("CFG: nrates must be >= 0");
    const int lines_to_read = nrates == 0 ? 1 : nrates;
    for (int i = 0; i < lines_to_read; ++i) {
      const auto f = split(next("sample rate line"), ',');
      if (f.size() < 2) throw ComtradeError("CFG: sample rate line needs 'samp,endsamp'");
      SampleRate r;
      r.rate_hz = require_number(f[0], "sample rate", ln);
      r.end_sample = static_cast<long long>(require_number(f[1], "end sample", ln));
      cfg.rates.push_back(r);
    }
  }

  cfg.start = parse_timestamp(next("start timestamp"), cfg.rev_year);
  cfg.trigger = parse_timestamp(next("trigger timestamp"), cfg.rev_year);

  {
    const std::string ft = upper(trim(next("file type")));
    if (ft == "ASCII") {
      cfg.format = DataFormat::Ascii;
    } else if (ft == "BINARY") {
      cfg.format = DataFormat::Binary;
    } else if (ft == "BINARY32") {
      cfg.format = DataFormat::Binary32;
    } else if (ft == "FLOAT32") {
      cfg.format = DataFormat::Float32;
    } else {
      throw ComtradeError("CFG: unsupported file type '" + ft + "'");
    }
  }

  // Optional time multiplication factor (absent in 1991 files).
  while (ln < lines.size() && trim(lines[ln]).empty()) ++ln;
  if (ln < lines.size()) {
    double tm = 0.0;
    if (parse_number(lines[ln], tm) && tm > 0.0) {
      cfg.time_mult = tm;
      ++ln;
    }
  }
  // Remaining 2013 lines (time_code/local_code, tmq_code/leapsec) are not needed.
  return cfg;
}

Record parse_dat(const Config& cfg, const std::string& bytes) {
  Record rec;
  rec.cfg = cfg;
  const std::size_t n_a = cfg.analogs.size();
  const std::size_t n_d = cfg.digitals.size();
  rec.analog.assign(n_a, {});
  rec.digital.assign(n_d, {});
  std::vector<double> stamps;  // raw timestamps, NaN when missing

  const long long declared = cfg.declared_samples();
  if (declared > 0) {
    for (auto& v : rec.analog) v.reserve(static_cast<std::size_t>(declared));
    for (auto& v : rec.digital) v.reserve(static_cast<std::size_t>(declared));
    stamps.reserve(static_cast<std::size_t>(declared));
  }

  if (cfg.format == DataFormat::Ascii) {
    const auto lines = split_lines(bytes);
    std::size_t line_no = 0;
    for (const auto& raw_line : lines) {
      ++line_no;
      if (trim(raw_line).empty()) continue;
      const auto f = split(raw_line, ',');
      if (f.size() < 2 + n_a + n_d) {
        std::ostringstream msg;
        msg << "DAT line " << line_no << ": expected " << 2 + n_a + n_d << " fields, got "
            << f.size();
        throw ComtradeError(msg.str());
      }
      double ts = kNaN;
      parse_number(f[1], ts);
      stamps.push_back(ts);
      for (std::size_t c = 0; c < n_a; ++c) {
        double raw = kNaN;
        const bool ok = parse_number(f[2 + c], raw);
        const bool missing = !ok || (cfg.rev_year <= 1999 && raw == 99999.0);
        const auto& ch = cfg.analogs[c];
        rec.analog[c].push_back(missing ? kNaN : ch.a * raw + ch.b);
      }
      for (std::size_t c = 0; c < n_d; ++c) {
        double v = 0.0;
        parse_number(f[2 + n_a + c], v);
        rec.digital[c].push_back(v != 0.0 ? 1 : 0);
      }
    }
  } else {
    const std::size_t a_bytes = cfg.format == DataFormat::Binary ? 2 : 4;
    const std::size_t d_words = (n_d + 15) / 16;
    const std::size_t rec_size = 8 + n_a * a_bytes + 2 * d_words;
    const std::size_t n = bytes.size() / rec_size;
    if (bytes.size() % rec_size != 0) {
      rec.warnings.push_back("DAT size is not a whole number of records; trailing bytes ignored");
    }
    const auto* data = reinterpret_cast<const unsigned char*>(bytes.data());
    for (std::size_t r = 0; r < n; ++r) {
      const unsigned char* p = data + r * rec_size;
      const std::uint32_t ts = le32(p + 4);
      stamps.push_back(ts == 0xFFFFFFFFu ? kNaN : static_cast<double>(ts));
      const unsigned char* q = p + 8;
      for (std::size_t c = 0; c < n_a; ++c) {
        const auto& ch = cfg.analogs[c];
        double value = kNaN;
        if (cfg.format == DataFormat::Binary) {
          const auto raw = static_cast<std::int16_t>(le16(q));
          if (raw != INT16_MIN) value = ch.a * raw + ch.b;
        } else if (cfg.format == DataFormat::Binary32) {
          const auto raw = static_cast<std::int32_t>(le32(q));
          if (raw != INT32_MIN) value = ch.a * raw + ch.b;
        } else {
          const std::uint32_t bits = le32(q);
          float f = 0.0f;
          static_assert(sizeof(float) == 4, "FLOAT32 requires 32-bit float");
          std::memcpy(&f, &bits, 4);
          if (!std::isnan(f)) value = ch.a * static_cast<double>(f) + ch.b;
        }
        rec.analog[c].push_back(value);
        q += a_bytes;
      }
      for (std::size_t c = 0; c < n_d; ++c) {
        const std::uint16_t word = le16(q + 2 * (c / 16));
        rec.digital[c].push_back(static_cast<std::uint8_t>((word >> (c % 16)) & 1u));
      }
    }
  }

  const std::size_t k_total = stamps.size();
  if (declared > 0 && static_cast<long long>(k_total) != declared) {
    std::ostringstream msg;
    msg << "DAT has " << k_total << " samples but CFG declares " << declared;
    rec.warnings.push_back(msg.str());
  }
  if (k_total == 0) throw ComtradeError("DAT contains no samples");

  rec.time_s.resize(k_total);
  const bool use_rates = !cfg.rates.empty() && cfg.rates.front().rate_hz > 0.0;
  if (use_rates) {
    std::size_t seg = 0;
    double t = 0.0;
    for (std::size_t k = 0; k < k_total; ++k) {
      if (k > 0) {
        const auto sample_number = static_cast<long long>(k + 1);  // 1-based
        while (seg + 1 < cfg.rates.size() && sample_number > cfg.rates[seg].end_sample) ++seg;
        t += 1.0 / cfg.rates[seg].rate_hz;
      }
      rec.time_s[k] = t;
    }
    if (cfg.rates.size() > 1) {
      rec.warnings.push_back("record uses multiple sampling rates; analysis assumes the first");
    }
  } else {
    if (std::isnan(stamps.front())) {
      throw ComtradeError("no sampling rate in CFG and DAT timestamps are missing");
    }
    const double t0 = stamps.front();
    for (std::size_t k = 0; k < k_total; ++k) {
      if (std::isnan(stamps[k])) {
        throw ComtradeError("no sampling rate in CFG and a DAT timestamp is missing");
      }
      rec.time_s[k] = (stamps[k] - t0) * cfg.time_mult * 1e-6;
    }
  }
  return rec;
}

std::string read_file(const std::filesystem::path& p) {
  std::ifstream in(p, std::ios::binary);
  if (!in) throw ComtradeError("cannot open " + p.string());
  std::ostringstream ss;
  ss << in.rdbuf();
  return ss.str();
}

Record load_record(const std::filesystem::path& cfg_path) {
  const Config cfg = parse_cfg(read_file(cfg_path));
  std::filesystem::path dat;
  for (const char* ext : {".dat", ".DAT", ".Dat"}) {
    auto candidate = cfg_path;
    candidate.replace_extension(ext);
    if (std::filesystem::exists(candidate)) {
      dat = candidate;
      break;
    }
  }
  if (dat.empty()) throw ComtradeError("no .dat file next to " + cfg_path.string());
  return parse_dat(cfg, read_file(dat));
}

}  // namespace rea
