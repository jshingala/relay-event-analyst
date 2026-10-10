#include <gtest/gtest.h>

#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>

#include "rea/comtrade.hpp"

namespace {

const char* kCfg1999 =
    "STATION_1,RELAY_7,1999\r\n"
    "5,3A,2D\r\n"
    "1,IA,A,LINE1,A,0.01,0,0,-32767,32767,1200,5,S\r\n"
    "2,IB,B,LINE1,A,0.01,0.5,0,-32767,32767,1200,5,S\r\n"
    "3,VA,A,BUS1,kV,0.001,0,0,-32767,32767,115000,115,P\r\n"
    "1,TRIP,,,0\r\n"
    "2,52A,,,1\r\n"
    "60\r\n"
    "1\r\n"
    "1920,4\r\n"
    "09/10/2026,14:05:31.250000\r\n"
    "09/10/2026,14:05:31.300000\r\n"
    "%FORMAT%\r\n"
    "1\r\n";

std::string cfg_with(const std::string& fmt) {
  std::string s = kCfg1999;
  s.replace(s.find("%FORMAT%"), 8, fmt);
  return s;
}

void put16(std::string& b, std::uint16_t v) {
  b.push_back(static_cast<char>(v & 0xFF));
  b.push_back(static_cast<char>(v >> 8));
}

void put32(std::string& b, std::uint32_t v) {
  for (int i = 0; i < 4; ++i) b.push_back(static_cast<char>((v >> (8 * i)) & 0xFF));
}

}  // namespace

TEST(Comtrade, ParsesCfg1999) {
  const auto cfg = rea::parse_cfg(cfg_with("ASCII"));
  EXPECT_EQ(cfg.station, "STATION_1");
  EXPECT_EQ(cfg.device, "RELAY_7");
  EXPECT_EQ(cfg.rev_year, 1999);
  ASSERT_EQ(cfg.analogs.size(), 3u);
  ASSERT_EQ(cfg.digitals.size(), 2u);
  EXPECT_EQ(cfg.analogs[1].id, "IB");
  EXPECT_DOUBLE_EQ(cfg.analogs[1].b, 0.5);
  EXPECT_EQ(cfg.analogs[2].ps, 'P');
  EXPECT_DOUBLE_EQ(cfg.analogs[2].primary, 115000.0);
  EXPECT_EQ(cfg.digitals[1].id, "52A");
  EXPECT_EQ(cfg.digitals[1].normal_state, 1);
  EXPECT_DOUBLE_EQ(cfg.line_freq_hz, 60.0);
  ASSERT_EQ(cfg.rates.size(), 1u);
  EXPECT_DOUBLE_EQ(cfg.rates[0].rate_hz, 1920.0);
  EXPECT_EQ(cfg.rates[0].end_sample, 4);
  EXPECT_TRUE(cfg.start.valid);
  EXPECT_EQ(cfg.start.day, 9);
  EXPECT_EQ(cfg.start.month, 10);
  EXPECT_EQ(cfg.start.year, 2026);
  EXPECT_EQ(cfg.format, rea::DataFormat::Ascii);
}

TEST(Comtrade, ParsesAsciiDat) {
  const auto cfg = rea::parse_cfg(cfg_with("ASCII"));
  const std::string dat =
      "1,0,100,200,66000,0,1\n"
      "2,521,-100,-200,-66000,1,1\n"
      "3,1042,99999,0,0,1,0\n"
      "4,1563,5,5,5,0,0\n";
  const auto rec = rea::parse_dat(cfg, dat);
  ASSERT_EQ(rec.num_samples(), 4u);
  EXPECT_DOUBLE_EQ(rec.analog[0][0], 1.0);
  EXPECT_DOUBLE_EQ(rec.analog[1][0], 2.5);
  EXPECT_DOUBLE_EQ(rec.analog[2][1], -66.0);
  EXPECT_TRUE(std::isnan(rec.analog[0][2]));  // 99999 marks missing data
  EXPECT_EQ(rec.digital[0][1], 1);
  EXPECT_EQ(rec.digital[1][3], 0);
  EXPECT_NEAR(rec.time_s[3], 3.0 / 1920.0, 1e-15);
  double trig = 0.0;
  ASSERT_TRUE(rec.trigger_offset_s(trig));
  EXPECT_NEAR(trig, 0.05, 1e-9);
  EXPECT_TRUE(rec.warnings.empty());
}

TEST(Comtrade, ParsesBinaryDat) {
  const auto cfg = rea::parse_cfg(cfg_with("BINARY"));
  std::string dat;
  const std::int16_t values[2][3] = {{100, -200, 32000}, {INT16_MIN, 7, -5}};
  const std::uint16_t words[2] = {0x0001, 0x0002};
  for (int r = 0; r < 2; ++r) {
    put32(dat, static_cast<std::uint32_t>(r + 1));
    put32(dat, static_cast<std::uint32_t>(r * 521));
    for (int c = 0; c < 3; ++c) put16(dat, static_cast<std::uint16_t>(values[r][c]));
    put16(dat, words[r]);
  }
  const auto rec = rea::parse_dat(cfg, dat);
  ASSERT_EQ(rec.num_samples(), 2u);
  EXPECT_DOUBLE_EQ(rec.analog[0][0], 1.0);
  EXPECT_DOUBLE_EQ(rec.analog[1][0], -2.0 + 0.5);
  EXPECT_DOUBLE_EQ(rec.analog[2][0], 32.0);
  EXPECT_TRUE(std::isnan(rec.analog[0][1]));  // 0x8000 marks missing data
  EXPECT_EQ(rec.digital[0][0], 1);
  EXPECT_EQ(rec.digital[1][0], 0);
  EXPECT_EQ(rec.digital[0][1], 0);
  EXPECT_EQ(rec.digital[1][1], 1);
  ASSERT_FALSE(rec.warnings.empty());  // 2 samples but CFG declares 4
}

TEST(Comtrade, ParsesBinary32AndFloat32) {
  for (const char* fmt : {"BINARY32", "FLOAT32"}) {
    const auto cfg = rea::parse_cfg(cfg_with(fmt));
    std::string dat;
    for (int r = 0; r < 4; ++r) {
      put32(dat, static_cast<std::uint32_t>(r + 1));
      put32(dat, 0xFFFFFFFFu);
      for (int c = 0; c < 3; ++c) {
        if (std::string(fmt) == "FLOAT32") {
          const float f = 1000.5f * static_cast<float>(c + 1);
          std::uint32_t bits;
          std::memcpy(&bits, &f, 4);
          put32(dat, bits);
        } else {
          put32(dat, static_cast<std::uint32_t>(100000 * (c + 1)));
        }
      }
      put16(dat, 0);
    }
    const auto rec = rea::parse_dat(cfg, dat);
    ASSERT_EQ(rec.num_samples(), 4u) << fmt;
    if (std::string(fmt) == "FLOAT32") {
      EXPECT_NEAR(rec.analog[0][0], 10.005, 1e-9);
    } else {
      EXPECT_DOUBLE_EQ(rec.analog[0][0], 1000.0);
      EXPECT_DOUBLE_EQ(rec.analog[1][0], 2000.5);
    }
    EXPECT_TRUE(rec.warnings.empty()) << fmt;
  }
}

TEST(Comtrade, Parses1991Layout) {
  const std::string cfg_text =
      "OLD STATION,DEV1\n"
      "2,1A,1D\n"
      "1,IA,A,,A,0.5,0,0,-2048,2047\n"
      "1,TRIP,0\n"
      "60\n"
      "1\n"
      "960,3\n"
      "10/09/26,14:05:31.250000\n"
      "10/09/26,14:05:31.250000\n"
      "ASCII\n";
  const auto cfg = rea::parse_cfg(cfg_text);
  EXPECT_EQ(cfg.rev_year, 1991);
  EXPECT_FALSE(cfg.analogs[0].has_ratio);
  EXPECT_EQ(cfg.digitals[0].id, "TRIP");
  EXPECT_EQ(cfg.start.month, 10);
  EXPECT_EQ(cfg.start.day, 9);
  EXPECT_EQ(cfg.start.year, 2026);
  const auto rec = rea::parse_dat(cfg, "1,0,4,0\n2,0,-4,1\n3,0,2,1\n");
  EXPECT_DOUBLE_EQ(rec.analog[0][1], -2.0);
  EXPECT_NEAR(rec.time_s[2], 2.0 / 960.0, 1e-15);
}

TEST(Comtrade, TimestampTimeBaseWhenNoSampleRate) {
  const std::string cfg_text =
      "S,D,1999\n1,1A,0D\n1,IA,A,,A,1,0,0,-1,1,1,1,S\n60\n0\n0,3\n"
      "01/01/2026,00:00:00.000000\n01/01/2026,00:00:00.000000\nASCII\n2\n";
  const auto cfg = rea::parse_cfg(cfg_text);
  EXPECT_DOUBLE_EQ(cfg.time_mult, 2.0);
  const auto rec = rea::parse_dat(cfg, "1,0,1\n2,500,2\n3,1000,3\n");
  EXPECT_NEAR(rec.time_s[1], 0.001, 1e-12);  // 500 us x timemult 2
  EXPECT_NEAR(rec.time_s[2], 0.002, 1e-12);
}

TEST(Comtrade, ReportsBadLines) {
  const std::string bad = "S,D,1999\n1,1A,0D\n1,IA,A\n60\n";
  try {
    rea::parse_cfg(bad);
    FAIL() << "expected ComtradeError";
  } catch (const rea::ComtradeError& e) {
    EXPECT_NE(std::string(e.what()).find("line 3"), std::string::npos) << e.what();
  }
  EXPECT_THROW(rea::parse_cfg(cfg_with("XML")), rea::ComtradeError);
  const auto cfg = rea::parse_cfg(cfg_with("ASCII"));
  EXPECT_THROW(rea::parse_dat(cfg, "1,0,1\n"), rea::ComtradeError);
}

TEST(Comtrade, LoadsFilePairWithUppercaseDatExtension) {
  const auto dir = std::filesystem::temp_directory_path() / "rea_comtrade_test";
  std::filesystem::create_directories(dir);
  {
    std::ofstream(dir / "ev.cfg") << cfg_with("ASCII");
    std::ofstream(dir / "ev.DAT") << "1,0,1,1,1,0,1\n2,0,1,1,1,0,1\n3,0,1,1,1,0,1\n4,0,1,1,1,0,1\n";
  }
  const auto rec = rea::load_record(dir / "ev.cfg");
  EXPECT_EQ(rec.num_samples(), 4u);
  std::filesystem::remove_all(dir);
}
