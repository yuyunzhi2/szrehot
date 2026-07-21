#!/usr/bin/env bash
# szrehot 一行安装脚本
# 用法: curl -fsSL https://raw.githubusercontent.com/zycyyyya/szrehot/main/install.sh | bash

set -e

# 检测 Skill 目录
SKILL_DIR=""

# 优先检测 WorkBuddy
if [ -d "C:\Users\Administrator/.workbuddy/skills" ]; then
    SKILL_DIR="C:\Users\Administrator/.workbuddy/skills/szrehot"
# Claude Code
elif [ -d "C:\Users\Administrator/.claude/skills" ]; then
    SKILL_DIR="C:\Users\Administrator/.claude/skills/szrehot"
# Codex CLI
elif [ -d "C:\Users\Administrator/.codex/skills" ]; then
    SKILL_DIR="C:\Users\Administrator/.codex/skills/szrehot"
# 通用回退
else
    SKILL_DIR="C:\Users\Administrator/.skills/szrehot"
fi

echo "=== szrehot 安装 ==="
echo "目标目录: "

# 克隆仓库
if [ -d "" ]; then
    echo "检测到已有安装，更新中..."
    cd "" && git pull --ff-only || { echo "更新失败，尝试重新克隆"; rm -rf ""; }
fi

if [ ! -d "" ]; then
    echo "克隆仓库..."
    git clone https://github.com/zycyyyya/szrehot.git ""
fi

# 检查 Python 依赖
echo ""
echo "检查依赖..."

if command -v python3 &>/dev/null; then
    if python3 -c "import feedparser" 2>/dev/null; then
        echo "✅ feedparser 已安装"
    else
        echo "⚠️  feedparser 未安装（脚本模式需要）"
        echo "   安装命令: pip install feedparser"
    fi
else
    echo "⚠️  Python3 未安装（脚本模式需要）"
fi

# 检查 Tavily（可选）
echo ""
echo "可选依赖："
if [ -f "/codex_env.json" ]; then
    TAVILY=
    if [ -n "" ]; then
        echo "✅ Tavily API Key 已配置（微信全文兜底）"
    else
        echo "⚠️  Tavily API Key 未配置（微信全文兜底需要，可选）"
        echo "   编辑 /codex_env.json 添加 tavily_api_key"
    fi
fi

echo ""
echo "✅ szrehot 安装完成！"
echo ""
echo "使用方法："
echo "  在对话中说「深圳楼市有什么」「深圳房价」「深圳土拍」「深圳城市更新」"
echo "  Skill 会自动触发并聚合多个数据源输出中文简报"
echo ""
echo "脚本模式（可选）："
echo "  cd "
echo "  pip install feedparser"
echo "  python scripts/rss_fetcher.py --output ./data --days 1"
echo "  python scripts/daily_generator.py --input ./data --output ./daily --markdown"
echo "  python scripts/weixin_fetcher.py --output ./data --keywords '深圳楼市 2026年7月' --with-tavily"
