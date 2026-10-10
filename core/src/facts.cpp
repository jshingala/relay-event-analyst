#include "rea/facts.hpp"

#include <cmath>
#include <cstdio>
#include <sstream>
#include <stdexcept>

namespace rea {

std::string format_fixed(double value, int decimals) {
  char buf[64];
  std::snprintf(buf, sizeof(buf), "%.*f", decimals, value);
  std::string s(buf);
  if (s == "-0" || s.rfind("-0.", 0) == 0) {
    // Avoid "-0.00" for values that round to zero.
    bool all_zero = true;
    for (char c : s) {
      if (c >= '1' && c <= '9') all_zero = false;
    }
    if (all_zero) s.erase(0, 1);
  }
  return s;
}

std::string json_escape(const std::string& s) {
  std::string out;
  out.reserve(s.size() + 8);
  for (const char ch : s) {
    const auto c = static_cast<unsigned char>(ch);
    switch (c) {
      case '"': out += "\\\""; break;
      case '\\': out += "\\\\"; break;
      case '\n': out += "\\n"; break;
      case '\r': out += "\\r"; break;
      case '\t': out += "\\t"; break;
      default:
        if (c < 0x20) {
          char buf[8];
          std::snprintf(buf, sizeof(buf), "\\u%04x", c);
          out += buf;
        } else {
          out.push_back(ch);
        }
    }
  }
  return out;
}

std::string Fact::display() const {
  switch (kind) {
    case FactKind::Number: return format_fixed(number, decimals);
    case FactKind::Text: return text;
    case FactKind::Flag: return flag ? "yes" : "no";
    case FactKind::Missing: return "unknown";
  }
  return "";
}

void FactSheet::add(Fact f) {
  for (auto& existing : facts_) {
    if (existing.id == f.id) {
      existing = std::move(f);
      return;
    }
  }
  facts_.push_back(std::move(f));
}

void FactSheet::number(const std::string& id, double value, const std::string& unit, int decimals,
                       const std::string& label, const std::string& method, int priority) {
  if (!std::isfinite(value)) {
    missing(id, label, "value could not be computed (not finite)", priority);
    return;
  }
  Fact f;
  f.id = id;
  f.kind = FactKind::Number;
  f.decimals = decimals;
  // Store the value exactly as displayed so every consumer sees the same number.
  f.number = std::stod(format_fixed(value, decimals));
  f.unit = unit;
  f.label = label;
  f.method = method;
  f.priority = priority;
  add(std::move(f));
}

void FactSheet::text(const std::string& id, const std::string& value, const std::string& label,
                     const std::string& method, int priority) {
  Fact f;
  f.id = id;
  f.kind = FactKind::Text;
  f.text = value;
  f.label = label;
  f.method = method;
  f.priority = priority;
  add(std::move(f));
}

void FactSheet::flag(const std::string& id, bool value, const std::string& label,
                     const std::string& method, int priority) {
  Fact f;
  f.id = id;
  f.kind = FactKind::Flag;
  f.flag = value;
  f.label = label;
  f.method = method;
  f.priority = priority;
  add(std::move(f));
}

void FactSheet::missing(const std::string& id, const std::string& label, const std::string& reason,
                        int priority) {
  Fact f;
  f.id = id;
  f.kind = FactKind::Missing;
  f.label = label;
  f.reason = reason;
  f.priority = priority;
  add(std::move(f));
}

void FactSheet::warn(const std::string& message) { warnings_.push_back(message); }

void FactSheet::meta(const std::string& key, double value) { meta_[key] = value; }

const Fact* FactSheet::find(const std::string& id) const {
  for (const auto& f : facts_) {
    if (f.id == id) return &f;
  }
  return nullptr;
}

std::string FactSheet::to_json(bool pretty) const {
  const char* nl = pretty ? "\n" : "";
  const std::string ind1 = pretty ? "  " : "";
  const std::string ind2 = pretty ? "    " : "";
  const char* sp = pretty ? " " : "";
  std::ostringstream o;
  o << "{" << nl;
  o << ind1 << "\"schema\":" << sp << "\"rea.facts/1\"," << nl;
  o << ind1 << "\"facts\":" << sp << "[" << nl;
  for (std::size_t i = 0; i < facts_.size(); ++i) {
    const auto& f = facts_[i];
    o << ind2 << "{\"id\":" << sp << "\"" << json_escape(f.id) << "\"," << sp;
    switch (f.kind) {
      case FactKind::Number:
        o << "\"kind\":" << sp << "\"number\"," << sp << "\"value\":" << sp
          << format_fixed(f.number, f.decimals) << "," << sp << "\"display\":" << sp << "\""
          << format_fixed(f.number, f.decimals) << "\"," << sp << "\"decimals\":" << sp
          << f.decimals << "," << sp << "\"unit\":" << sp << "\"" << json_escape(f.unit) << "\",";
        break;
      case FactKind::Text:
        o << "\"kind\":" << sp << "\"text\"," << sp << "\"value\":" << sp << "\""
          << json_escape(f.text) << "\"," << sp << "\"display\":" << sp << "\""
          << json_escape(f.text) << "\",";
        break;
      case FactKind::Flag:
        o << "\"kind\":" << sp << "\"flag\"," << sp << "\"value\":" << sp
          << (f.flag ? "true" : "false") << "," << sp << "\"display\":" << sp << "\""
          << (f.flag ? "yes" : "no") << "\",";
        break;
      case FactKind::Missing:
        o << "\"kind\":" << sp << "\"missing\"," << sp << "\"value\":" << sp << "null," << sp
          << "\"reason\":" << sp << "\"" << json_escape(f.reason) << "\",";
        break;
    }
    o << sp << "\"label\":" << sp << "\"" << json_escape(f.label) << "\"," << sp;
    if (!f.method.empty()) o << "\"method\":" << sp << "\"" << json_escape(f.method) << "\"," << sp;
    o << "\"priority\":" << sp << f.priority << "}";
    o << (i + 1 < facts_.size() ? "," : "") << nl;
  }
  o << ind1 << "]," << nl;
  o << ind1 << "\"warnings\":" << sp << "[";
  for (std::size_t i = 0; i < warnings_.size(); ++i) {
    o << (i ? "," : "") << nl << ind2 << "\"" << json_escape(warnings_[i]) << "\"";
  }
  o << (warnings_.empty() ? "" : nl) << (warnings_.empty() ? "" : ind1) << "]," << nl;
  o << ind1 << "\"meta\":" << sp << "{";
  std::size_t i = 0;
  for (const auto& [k, v] : meta_) {
    o << (i++ ? "," : "") << nl << ind2 << "\"" << json_escape(k) << "\":" << sp
      << (std::isfinite(v) ? format_fixed(v, 3) : "null");
  }
  o << (meta_.empty() ? "" : nl) << (meta_.empty() ? "" : ind1) << "}" << nl;
  o << "}" << nl;
  return o.str();
}

}  // namespace rea
