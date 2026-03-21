---
title: "A股智能交易系统 v2.0 架构全景"
topic: "technical/system-architecture"
data_type: "system/structure + cycle/loop + relationships"
complexity: "complex"
point_count: 12
source_language: "zh"
user_language: "zh"
---

## Main Topic
A股智能交易系统的完整架构，展示6大子系统（策略/监控/执行/复盘/选股/调度）的关系、每日运行周期、以及AI与系统之间的分工。

## Learning Objectives
After viewing this infographic, the viewer should understand:
1. 系统由哪6个核心模块组成，各自的职责是什么
2. 每个交易日从09:20到15:45的完整运行流程
3. AI（离线辅助）和系统（实时自主）之间的分工边界

## Target Audience
- **Knowledge Level**: Intermediate（了解股票交易基础，需理解系统架构）
- **Context**: 开发者/用户需要理解系统整体架构和运行机制
- **Expectations**: 一图了解整个系统的模块、流程、AI角色

## Content Type Analysis
- **Data Structure**: 系统架构（模块关系）+ 时间循环（日度周期）+ 分域关系（AI vs System）
- **Key Relationships**: 策略→监控→执行 的决策链；复盘→选股→策略 的反馈环；AI离线调优 vs 系统自主运行
- **Visual Opportunities**: 模块图标、数据流箭头、时间轴、AI/System分域

## Key Data Points (Verbatim)
- "初始资金 ¥1,000,000"
- "每1分钟循环"
- "每30分钟飞书快报 + 策略快速调整"
- "7源筛选"
- "6大子系统"
- "09:20 盘前 → 09:30-15:00 盘中 → 15:05 报告 → 15:30 复盘 → 15:45 选股"

## Layout × Style Signals
- Content type: system/structure + cycle → suggests circular-flow or bento-grid
- Tone: technical, professional → suggests technical-schematic
- Audience: developers → suggests technical-schematic or corporate-memphis
- Complexity: complex (12+ points) → suggests bento-grid for density

## Design Instructions (from user input)
- 需要包含: 架构、运行过程、AI和系统之间的关系
- 中文文字
- 用于README展示

## Recommended Combinations
1. **circular-flow + technical-schematic** (Recommended): 展示每日循环为主，蓝色工程风格，模块围绕中心"交易策略"旋转，专业感强
2. **hub-spoke + technical-schematic**: 以策略为中心hub，5个子系统为spoke，AI和System分区
3. **bento-grid + corporate-memphis**: 多面板展示，适合信息量大的全景图
