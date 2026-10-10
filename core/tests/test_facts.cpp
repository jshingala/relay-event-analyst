#include <gtest/gtest.h>

#include <cmath>
#include <limits>

#include "rea/facts.hpp"

TEST(Facts, NumbersAreStoredExactlyAsDisplayed) {
  rea::FactSheet f;
  f.number("x", 12.3456, "A", 2, "X", "m");
  ASSERT_NE(f.find("x"), nullptr);
  EXPECT_EQ(f.find("x")->display(), "12.35");
  EXPECT_DOUBLE_EQ(f.find("x")->number, 12.35);
  f.number("z", -0.001, "ms", 2, "Z", "m");
  EXPECT_EQ(f.find("z")->display(), "0.00");
}

TEST(Facts, NonFiniteBecomesMissing) {
  rea::FactSheet f;
  f.number("bad", std::numeric_limits<double>::quiet_NaN(), "A", 2, "Bad", "m");
  EXPECT_EQ(f.find("bad")->kind, rea::FactKind::Missing);
}

TEST(Facts, DuplicateIdReplaces) {
  rea::FactSheet f;
  f.text("a", "one", "A", "m");
  f.text("a", "two", "A", "m");
  EXPECT_EQ(f.facts().size(), 1u);
  EXPECT_EQ(f.find("a")->text, "two");
}

TEST(Facts, JsonIsEscapedAndComplete) {
  rea::FactSheet f;
  f.text("t", "say \"hi\"\n", "Quote", "m", 1);
  f.missing("m", "Missing", "no channel", 1);
  f.flag("b", true, "Bool", "m");
  f.warn("w1");
  f.meta("analyze_ms", 1.5);
  const std::string j = f.to_json(false);
  EXPECT_NE(j.find("\"schema\":\"rea.facts/1\""), std::string::npos);
  EXPECT_NE(j.find("say \\\"hi\\\"\\n"), std::string::npos);
  EXPECT_NE(j.find("\"kind\":\"missing\",\"value\":null,\"reason\":\"no channel\""), std::string::npos);
  EXPECT_NE(j.find("\"value\":true"), std::string::npos);
  EXPECT_NE(j.find("\"analyze_ms\":1.500"), std::string::npos);
  EXPECT_EQ(rea::json_escape(std::string("\x01")), "\\u0001");
}
