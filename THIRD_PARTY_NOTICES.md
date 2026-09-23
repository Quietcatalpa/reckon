# 第三方许可说明

盘算 Reckon 本身按 [MIT](LICENSE) 发布。它用到、以及 Windows 发布包里附带的第三方组件如下。

## Laya 决策模型（Convai Innovations）

- 模型权重：[convaiinnovations/laya-multilingual](https://huggingface.co/convaiinnovations/laya)，Apache License 2.0。
  发布包 `model/` 文件夹里的 `model.fp16.onnx` 是由原权重转换而来：导出为 ONNX 格式，权重矩阵改用半精度存储
  （加载时换回单精度计算）。`tokenizer.json` 为原模型附带的分词器，未修改。
- 代码：`reckon/laya_onnx.py` 中拼接模型输入的部分（`_render_options`、`build_sequence`）移植自
  [laya](https://pypi.org/project/laya/) 0.3.6 的 `laya/common.py`，Apache License 2.0，有改动（去掉 torch 依赖，只保留 choice 问题）。

Apache License 2.0 全文见 [licenses/Apache-2.0.txt](licenses/Apache-2.0.txt)。

## 发布包里附带的运行库

| 组件 | 许可证 |
|---|---|
| Python | PSF License |
| onnxruntime | MIT |
| onnx | Apache-2.0 |
| tokenizers（Hugging Face） | Apache-2.0 |
| NumPy | BSD-3-Clause |
| Send2Trash | BSD-3-Clause |
| pywin32 | PSF License |
| python-docx、python-pptx、openpyxl | MIT |
| lxml | BSD-3-Clause |
| Pillow | MIT-CMU (HPND) |
| Tcl/Tk | Tcl/Tk License（BSD 风格） |
| PyInstaller 引导程序 | GPL-2.0 附带例外条款，允许随任意许可证的程序分发 |

## 源码运行时的可选依赖

- torch、transformers、laya：只在源码运行、并且没有 ONNX 模型时才用到，不随发布包分发。
- PyMuPDF：AGPL-3.0，可选安装，用于读取 PDF 内容；不随发布包分发（发布版对 PDF 只看文件信息）。
