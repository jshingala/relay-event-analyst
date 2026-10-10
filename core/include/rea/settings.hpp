// Relay settings in the familiar `NAME := value` style used on relay settings
// sheets, e.g.
//
//     51P1P  := 6.00     51P1C  := U3       51P1TD := 2.00
//
// Several pairs may share a line. Text after '#' is a comment. Names are
// case-insensitive; values may be quoted. "OFF" disables a numeric setting.
#pragma once

#include <filesystem>
#include <map>
#include <optional>
#include <string>

namespace rea {

class Settings {
 public:
  static Settings parse(const std::string& text);
  static Settings load(const std::filesystem::path& path);

  bool has(const std::string& key) const;
  std::optional<std::string> get(const std::string& key) const;
  // Missing -> fallback. "OFF" -> nullopt. Throws std::invalid_argument if unparsable.
  std::optional<double> number(const std::string& key, std::optional<double> fallback) const;
  std::string text(const std::string& key, const std::string& fallback) const;
  bool flag(const std::string& key, bool fallback) const;  // Y/N, YES/NO, 1/0, TRUE/FALSE
  void set(const std::string& key, const std::string& value);
  const std::map<std::string, std::string>& all() const { return kv_; }

 private:
  std::map<std::string, std::string> kv_;
};

}  // namespace rea
