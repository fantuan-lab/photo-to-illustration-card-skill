# Photo to Illustration Card

一个把人物、宠物或物品照片转换为保真手绘插画，或生成“上方原图、下方插画”明信片的 Codex Skill。

它将插画生成与最终排版分开：图像模型只负责无文字插画层，Python 合成脚本负责保留原图、准确文字、版式和可复核的 JSON sidecar。

## 安装

```bash
git clone https://github.com/fantuan-lab/photo-to-illustration-card-skill.git ~/.codex/skills/photo-to-illustration-card
```

合成与检查脚本需要 Python 3.9+ 和 `Pillow>=9.1.0`。如果当前环境没有 Pillow，可运行：

```bash
python3 -m pip install -r ~/.codex/skills/photo-to-illustration-card/requirements.txt
```

脚本会自动寻找常见的 macOS/Linux 字体；在 Windows 或精简容器中，请通过 `compose_card.py --font <字体文件>` 指定可加载的 TTF、OTF 或 TTC 字体。Codex 内置生图路径不需要用户提供 API Key。

## 使用

在 Codex 中附上一张有权使用的照片，然后直接说：

```text
把这张照片做成复古插画明信片
```

Skill 默认保留原始照片，只对副本做 EXIF 方向修正、缩放或裁切；生成结果保存在当前项目，而不是 Skill 目录。

## 主要文件

- `SKILL.md`：工作流、边界和交付约定
- `scripts/compose_card.py`：确定性明信片合成器
- `scripts/check_output.py`：图片与 sidecar 校验器
- `references/prompt-contract.md`：插画提示词契约
- `references/qa-checklist.md`：确定性与视觉 QA
- `references/style-presets.md`：插画和版式预设
- `agents/openai.yaml`：Codex 界面元数据与调用策略
- `requirements.txt`：独立运行脚本所需的 Pillow 最低版本

源照片、生成图片和包含本机路径的 sidecar 不属于仓库内容。
