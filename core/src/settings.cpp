#include "rea/settings.hpp"

#include <cctype>
#include <cerrno>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <vector>

namespace rea {
namespace {

std::string trim(const std::string& s) {
  std::size_t b = 0;
  std::size_t e = s.size();
  while (b < e && std::isspace(static_cast<unsigned char>(s[b]))) ++b;
  while (e > b && std::isspace(static_cast<unsigned char>(s[e - 1]))) --e;
  return s.substr(b, e - b);
}

std::string upper(std::string s) {
  for (auto& c : s) c = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
  return s;
}

std::string unquote(std::string v) {
  v = trim(v);
  if (v.size() >= 2 && ((v.front() == '"' && v.back() == '"') ||
                        (v.front() == '\'' && v.back() == '\''))) {
    v = v.substr(1, v.size() - 2);
  }
  return trim(v);
}

// Split "A := 1   B := x y" into pairs. The text between two ':=' markers holds
// the previous value followed by the next key (the last whitespace token).
void parse_line(const std::string& line, std::map<std::string, std::string>& kv) {
  std::vector<std::string> segs;
  std::size_t start = 0;
  for (;;) {
    const auto pos = line.find(":=", start);
    if (pos == std::string::npos) {
      segs.push_back(line.substr(start));
      break;
    }
    segs.push_back(line.substr(start, pos - start));
    start = pos + 2;
  }
  if (segs.size() < 2) return;

  auto split_last_token = [](const std::string& s, std::string& head, std::string& tail) {
    const std::string t = trim(s);
    const auto sp = t.find_last_of(" \t");
    if (sp == std::string::npos) {
      head.clear();
      tail = t;
    } else {
      head = trim(t.substr(0, sp));
      tail = trim(t.substr(sp + 1));
    }
  };

  std::string head;
  std::string key;
  split_last_token(segs[0], head, key);
  for (std::size_t i = 1; i < segs.size(); ++i) {
    std::string value;
    std::string next_key;
    if (i + 1 < segs.size()) {
      split_last_token(segs[i], value, next_key);
    } else {
      value = trim(segs[i]);
    }
    if (!key.empty()) kv[upper(key)] = unquote(value);
    key = next_key;
  }
}

}  // namespace

Settings Settings::parse(const std::string& text) {
  Settings s;
  std::istringstream in(text);
  std::string line;
  while (std::getline(in, line)) {
    const auto hash = line.find('#');
    if (hash != std::string::npos) line = line.substr(0, hash);
    if (!line.empty() && line.back() == '\r') line.pop_back();
    parse_line(line, s.kv_);
  }
  return s;
}

Settings Settings::load(const std::filesystem::path& path) {
  std::ifstream in(path);
  if (!in) throw std::runtime_error("cannot open settings file " + path.string());
  std::ostringstream ss;
  ss << in.rdbuf();
  return parse(ss.str());
}

bool Settings::has(const std::string& key) const { return kv_.count(upper(key)) > 0; }

std::optional<std::string> Settings::get(const std::string& key) const {
  const auto it = kv_.find(upper(key));
  if (it == kv_.end()) return std::nullopt;
  return it->second;
}

std::optional<double> Settings::number(const std::string& key,
                                       std::optional<double> fallback) const {
  const auto v = get(key);
  if (!v || v->empty()) return fallback;
  if (upper(*v) == "OFF") return std::nullopt;
  char* end = nullptr;
  errno = 0;
  const double d = std::strtod(v->c_str(), &end);
  while (end && *end && std::isspace(static_cast<unsigned char>(*end))) ++end;
  if (end == v->c_str() || (end && *end) || errno == ERANGE) {
    throw std::invalid_argument("setting " + upper(key) + " is not a number: '" + *v + "'");
  }
  return d;
}

std::string Settings::text(const std::string& key, const std::string& fallback) const {
  const auto v = get(key);
  return v ? *v : fallback;
}

bool Settings::flag(const std::string& key, bool fallback) const {
  const auto v = get(key);
  if (!v) return fallback;
  const std::string u = upper(*v);
  if (u == "Y" || u == "YES" || u == "1" || u == "TRUE") return true;
  if (u == "N" || u == "NO" || u == "0" || u == "FALSE") return false;
  throw std::invalid_argument("setting " + upper(key) + " must be Y or N");
}

void Settings::set(const std::string& key, const std::string& value) { kv_[upper(key)] = value; }

}  // namespace rea
