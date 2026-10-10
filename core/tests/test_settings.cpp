#include <gtest/gtest.h>

#include "rea/settings.hpp"

TEST(Settings, ParsesRelayStyleSheet) {
  const auto s = rea::Settings::parse(
      "# feeder relay\n"
      "51P1P  := 6.00     51P1C  := U3       51P1TD := 2.00\n"
      "50P1P := OFF   DIR := F\n"
      "TRIPCH := \"OUT101\"\r\n"
      "RID := FEEDER 12 MAIN\n");
  EXPECT_DOUBLE_EQ(*s.number("51P1P", std::nullopt), 6.0);
  EXPECT_EQ(s.text("51p1c", ""), "U3");
  EXPECT_DOUBLE_EQ(*s.number("51P1TD", std::nullopt), 2.0);
  EXPECT_FALSE(s.number("50P1P", 1.0).has_value());  // OFF disables
  EXPECT_EQ(s.text("DIR", ""), "F");
  EXPECT_EQ(s.text("TRIPCH", ""), "OUT101");
  EXPECT_EQ(s.text("RID", ""), "FEEDER 12 MAIN");
  EXPECT_DOUBLE_EQ(*s.number("CTR", 120.0), 120.0);  // fallback when missing
}

TEST(Settings, FlagsAndErrors) {
  const auto s = rea::Settings::parse("51P1RS := Y\nX := N\nBAD := abc\n");
  EXPECT_TRUE(s.flag("51P1RS", false));
  EXPECT_FALSE(s.flag("X", true));
  EXPECT_TRUE(s.flag("MISSING", true));
  EXPECT_THROW(s.number("BAD", std::nullopt), std::invalid_argument);
  EXPECT_THROW(s.flag("BAD", false), std::invalid_argument);
}
