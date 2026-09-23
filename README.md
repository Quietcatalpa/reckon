<div align="center">

# 盘算 Reckon

**让本地决策模型先帮你把磁盘盘算一遍：哪些该删，哪些该留，东西该放哪。**

Windows 磁盘清理 + 文件整理 · 规则 + [Laya](https://huggingface.co/convaiinnovations/laya) 本地决策模型 ·
不联网 · 只进回收站 · 整理可一键撤销

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![Windows](https://img.shields.io/badge/Windows-10%20%7C%2011-0078D6?logo=windows&logoColor=white)
![Local](https://img.shields.io/badge/100%25-本地运行-2f6f5e)
![Model](https://img.shields.io/badge/模型-Laya%20多语言-6b4fbb)
![License](https://img.shields.io/badge/License-MIT-yellow)

<img alt="演示：清理时勾选重复文件和建议删除项移到回收站，整理时按现有分类给出去处、执行后一键撤销" src="docs/images/demo.gif" width="860">

<sub>演示模式录制，文件都是虚构的，也没有真正改动任何文件</sub>

</div>

## 它能做什么

- **🧹 清理**：扫描 C/D 盘，给每个候选文件一个「清理建议分」、理由和判断依据，分成
  建议删除 / 需要你看 / 建议保留 / 重复文件 / 缓存目录。勾选后移到回收站，删错了能找回。
- **🗂️ 整理**：把「下载」这类乱七八糟的文件夹交给它，它按你**已经分好类的文件夹**给出去处
  （比如「日本旅行攻略.pdf」→ `04_旅行\日本`），或按文件类型分好。预览、修改、确认后才移动，每次都能一键撤销。
- **📖 会读内容**：Word、PPT、Excel、PDF、文本、代码会读开头一段再判断，分得清「课件」和「软件压缩包」。
- **🔒 全在本机**：模型在你电脑上跑，文件名和内容不会上传到任何地方。

## 和普通清理工具有什么不同

普通清理工具只认得缓存和临时文件，对「下载文件夹里这个 1GB 的压缩包能不能删」无能为力。
这个工具会结合位置、类型、多久没动过、文件内容，让决策模型判断「这是学习资料，还是随时能重新下载的安装包」，
**每一项都告诉你为什么**，最后由你拍板。拿不准的一律放进「需要你看」，不替你冒险。

## 截图

**清理**：每一项都有清理建议分、理由和判断依据，拿不准的放进「需要你看」

<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/clean-dark.png">
  <img alt="清理页面：按清理建议分分成建议删除、需要你看、重复文件、缓存目录、建议保留" src="docs/images/clean-light.png" width="860">
</picture>
</div>

**整理**：按目标文件夹的现有分类给出去处，每项都能在下拉框里改

<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/organize-dark.png">
  <img alt="整理页面：每个文件显示建议的目标文件夹、理由和判断依据" src="docs/images/organize-light.png" width="860">
</picture>
</div>

> 截图和动图都来自演示模式，里面的文件是虚构的。你也可以运行 `python server.py --demo` 自己点一点：
> 清理和整理都只是模拟，不会改动任何文件。界面改了之后，用 `python docs/demo/capture.py` 可以重新生成截图和动图。

## 工作原理

**清理**

```mermaid
flowchart LR
    A[扫描磁盘] --> B{缓存目录?}
    B -- 是 --> R1[规则判断]
    B -- 否 --> C{重复文件?}
    C -- 是 --> R2[保留整理过的那份]
    C -- 否 --> D{安装包 / 图片视频?}
    D -- 是 --> R3[按放了多久]
    D -- 否 --> E[读开头一段内容] --> L[Laya：删除还是保留]
    R1 & R2 & R3 & L --> F[清理建议分 + 理由 + 依据] --> G[你勾选] --> H[移到回收站]
```

**整理**

```mermaid
flowchart LR
    S[交给它一个文件夹] --> N{名字里有已有文件夹名?}
    N -- 有 --> P[放进那个子文件夹]
    N -- 没有 --> K{命中关键词?}
    K -- 是 --> T[对应的顶层分类]
    K -- 否 --> L[Laya 在分类里选]
    L -- 把握 ≥ 50% --> T
    L -- 没把握 --> U[标成需要你选]
    P & T & U --> V[预览方案] --> W[确认后移动] --> X[可一键撤销]
```

判断规则、阈值和安全边界的细节见 [设计说明](docs/DESIGN.md)。

## 快速开始

需要 Windows 10/11 和 Python 3.10+。

```bash
pip install -r requirements.txt
pip install PyMuPDF   # 可选：让它也能读 PDF 内容（AGPL-3.0 许可）

# 下载 Laya 多语言模型（约 640MB，之后离线使用；不下载也能用，只是只按规则判断）
python -c "from huggingface_hub import snapshot_download; snapshot_download('convaiinnovations/laya-multilingual')"
```

然后双击 **`启动.bat`**，浏览器会打开 `http://127.0.0.1:8765/`。

| 想做什么 | 怎么做 |
|---|---|
| 先看看效果，不碰任何文件（操作都是模拟的） | `python server.py --demo` |
| 只用规则、不加载模型 | `python server.py --no-laya` |
| 整理某个文件夹 | 把它拖到 `整理.bat` 上 |
| 跑安全相关的测试 | `python -m unittest discover -s tests -v` |
| 在右键菜单里加「发送到 → 盘算 - 整理」 | 运行一次 `添加到右键发送到菜单.bat`（去掉：`python sendto.py --remove`） |

## 要多久

以一台约 30 万个文件、C+D 两个盘的电脑为例，一次完整扫描约 9 分钟，期间电脑照常用：

| 步骤 | 用时 |
|---|---|
| 遍历文件 | 约 30 秒 |
| 查找重复文件 | 约 2～3 分钟 |
| Laya 逐个判断（CPU，默认最多 300 个） | 约 2～5 分钟 |

赶时间就取消「查找重复文件」和「使用 Laya」，1 分钟左右出结果。扫描结果会保存，下次打开直接显示。

## 安全

- 清理只移到**回收站**：删除前会确认这一项一定能进回收站（本机硬盘、回收站没被关掉、没超过回收站容量上限），
  放不进去的直接跳过并告诉你原因，绝不会被 Windows 悄悄永久删除。
- 删除前再核对一次，扫描后被改过的文件不删；删重复副本前确认保留的那份还在。
- 系统目录、已安装的软件、conda 环境、`.git` 不扫描也不整理；程序组件（dll 等）不会被建议删除。
- 桌面/文档里的资料、聊天软件收到的文件，最多只到「需要你看」，不会被直接建议删除。
- 备份文件（.bak / .old 等）可能是唯一的一份，最多到「需要你看」；超大文件只抽样比对过的重复也只算「疑似」，删之前一律完整比对。
- 临时文件夹这类目录，扫描后里面有新改动就不动；直接交给「整理」的如果是软件目录（或在软件目录里面），会拒绝。
- 页面上的结果带编号：在别的页面重新扫描后，旧页面上的勾选会作废，不会错删成别的文件；删除、整理、撤销不会同时进行。
- 「清理建议分」是规则和模型加权算出来的排序依据，**不是**「删了没事」的概率，每项都会写明依据（完整比对 / 读过内容 / 只看文件信息 / 只按规则）。
- 整理有记录、可撤销：每移动一项之前先写记录，即使中途关掉程序也能撤销；有文件放不回去时可以处理后再试。
  重名自动加 ` (2)`，从不覆盖。
- 服务只监听 127.0.0.1，并校验随机令牌，其他网页调用不了。

## 常见问题

**会不会误删？**
工具本身不会删除任何东西，只会把你勾选的项移到回收站。确认没问题后你自己清空回收站，空间才真正腾出来。

**需要显卡吗？**
不需要。Laya 在 CPU 上每个文件约 0.4 秒，有 NVIDIA 显卡会自动用上，更快。

**Laya 判断得准吗？**
目前只做过一次**初步测试**：在「删除还是保留」这个问题上用 12 个真实文件试，答对 9 个，另外 3 个在 50% 左右，会落进「需要你看」。
样本太少，不能当作准确率；更系统的评估集在计划里。
所以最终分数是规则和模型一起算的，拿不准的交给你。主题分类它不太擅长，所以整理时优先用文件夹同名和关键词。

**为什么用 Laya，不用大模型或 Jev？**
Laya 是开源的、能完全在本机运行的「决策模型」：不生成文字，只在给定选项里选并给出概率，快而且不会答非所问。
[Jev](https://www.tomshardware.com/tech-industry/artificial-intelligence/typesafe-ais-jev-offers-an-alternative-to-llms-that-claims-to-be-193x-faster-and-445x-cheaper-system-one-type-model-is-bespoke-for-probabilistic-decision-making)
是同类的云端付费接口，用它就得把你的文件名和内容发出去。

**支持 macOS / Linux 吗？**
暂时只支持 Windows。

## 致谢

- [Laya](https://huggingface.co/convaiinnovations/laya)（Convai Innovations）：本地决策模型
- [Send2Trash](https://github.com/arsenetar/send2trash)、[python-docx](https://github.com/python-openxml/python-docx)、
  [python-pptx](https://github.com/scanny/python-pptx)、[openpyxl](https://openpyxl.readthedocs.io/)、[PyMuPDF](https://github.com/pymupdf/PyMuPDF)

## 许可证

[MIT](LICENSE)。Laya 模型权重另按 Apache-2.0 发布；可选依赖 PyMuPDF 为 AGPL-3.0，不随本项目分发。
