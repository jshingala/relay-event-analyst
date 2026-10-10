// The fact sheet is the only channel from the physics core to the narrator.
// Every number the explanation may contain is a Fact with an id, a unit and a
// display precision; anything the core could not determine is an explicit
// "missing" fact with a reason.
#pragma once

#include <map>
#include <string>
#include <vector>

namespace rea {

enum class FactKind { Number, Text, Flag, Missing };

struct Fact {
  std::string id;
  FactKind kind = FactKind::Missing;
  double number = 0.0;
  int decimals = 2;
  std::string text;
  bool flag = false;
  std::string unit;
  std::string label;
  std::string method;
  std::string reason;  // for Missing
  int priority = 2;    // 1 = headline fact, 2 = supporting detail, 3 = record metadata

  std::string display() const;
};

class FactSheet {
 public:
  void number(const std::string& id, double value, const std::string& unit, int decimals,
              const std::string& label, const std::string& method, int priority = 2);
  void text(const std::string& id, const std::string& value, const std::string& label,
            const std::string& method, int priority = 2);
  void flag(const std::string& id, bool value, const std::string& label, const std::string& method,
            int priority = 2);
  void missing(const std::string& id, const std::string& label, const std::string& reason,
               int priority = 2);
  void warn(const std::string& message);
  void meta(const std::string& key, double value);

  const Fact* find(const std::string& id) const;
  const std::vector<Fact>& facts() const { return facts_; }
  const std::vector<std::string>& warnings() const { return warnings_; }

  std::string to_json(bool pretty) const;

 private:
  void add(Fact f);
  std::vector<Fact> facts_;
  std::vector<std::string> warnings_;
  std::map<std::string, double> meta_;
};

std::string json_escape(const std::string& s);
std::string format_fixed(double value, int decimals);

}  // namespace rea
