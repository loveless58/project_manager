# OCR Baseline & Engine Selection

> **基线方法文档**(v0.4.0)
> 本文只保留可复现的引擎选择逻辑和合成评测方法。
> 无法追溯到当前仓库内合成生成器与原始输出的历史样本指标不作为性能证据。

---

## 1. 优先级链 (v0.4.0 起)

`_default_ocr_adapter` 决策顺序:

```
Vision (macOS Vision via Swift bridge)
  → RapidOCR (PaddleOCR v4 ONNX 量化版)
  → EasyOCR (纯 Python, 模型已下载)
  → Tesseract (需 brew install tesseract)
  → failed
```

| 引擎 | 触发条件 | 失败处理 |
|---|---|---|
| Vision | 永远先尝试 | swift bridge 失败 → RapidOCR |
| RapidOCR | Vision 失败 | 引擎初始化失败 / 返回 blocked → EasyOCR |
| EasyOCR | RapidOCR 失败 | 返回 blocked → Tesseract |
| Tesseract | EasyOCR 失败 | 返回 blocked → failed |

---

## 2. 重新生成的合成基线

逐样本历史指标已移除。此前记录无法证明由当前版本的合成生成器生成，也无法从仓库内的原始输出复算，因此不得通过改名继续引用。

重新生成基线时必须满足以下约束：

1. 仅使用运行时生成、与任何客户或历史文件不可关联的样本，编号统一为 `SYN-OCR-SAMPLE-*`。
2. 固定并记录生成器版本、随机种子、页数、渲染参数、引擎版本、硬件与重复次数。
3. 保存逐次原始结果供仓库内复算；对外文档只发布由这些结果重新聚合的不可逆汇总，不发布文件名、组织名、正文片段或可回链路径。
4. 至少报告中位数、分位数、失败率和样本规模；字符数只能作为覆盖代理，不能替代识别准确率。
5. 任一引擎失败都必须计入失败率，不能从分母中删除。

### 当前状态

当前仓库尚未提交满足上述约束的可复现原始输出，因此本节不发布速度、准确率或引擎优劣数字。后续出现的任何数字都必须明确标注为“重新生成的合成基线”或“不可逆聚合示例”，并能由仓库内工件复算。

### 聚合结果模板

| 指标 | 计算口径 | 发布条件 |
|---|---|---|
| 每页耗时 | 每次运行总耗时除以有效页数，再跨重复运行聚合 | 同一硬件、同一渲染参数 |
| 识别质量 | 对合成真值计算字符错误率与字段准确率 | 真值由生成器同步输出 |
| 稳定性 | 失败运行数除以全部运行数 | 失败不得剔除 |

## 3. 决策边界

当前 Vision → RapidOCR → EasyOCR → Tesseract 链仅表示可用性优先级与故障回退顺序，不构成性能领先声明。只有重新生成的合成基线完成并通过复算后，才能据此调整默认顺序或声称某个引擎解决了速度、中文识别等问题。

---

## 4. macOS Vision 桥接实现

### 为什么不用 PyObjC

PyObjC VNRecognizeTextRequest 在中文识别上有 bug(2026-07-23 实测):

- 不论 `recognitionLanguages` 怎么设 (`["zh-Hans"]` / `["zh-Hans", "en-US"]` / `["zh-CN"]`)
- 不论 `usesLanguageCorrection` 开 / 关
- 不论 `revision` 设什么 (默认 / 3)
- **所有中文输出都是乱码**(`SYN-OCR-ASCII-001` 等英文字符正常,中文全乱)

对照实验: 同一份 PNG 渲染 → Swift 直接调 Vision framework → 输出**完全正常**。
结论: PyObjC 桥接路径有 bug,只能走 Swift 子进程。

### Swift bridge 实现路径

```
[Python] tools/data_cleaning_tools.py:_ocr_with_vision_macos
  ↓ subprocess.run(swift_ocr_bridge, file_path, output_json)
[Swift] integrations/macos_vision_bridge/swift_ocr_bridge
  ├─ PDF → CGPDFDocument → CGContext 渲染 PNG @ 200dpi
  ├─ PNG / JPG → NSImage → TIFF → NSBitmapImageRep → CGImage
  ├─ VNRecognizeTextRequest (accurate, zh-Hans + en-US, languageCorrection=true)
  └─ JSON 输出: {status, engine, text, pages, elapsed_seconds}
```

### 文件清单

| 文件 | 用途 |
|---|---|
| `integrations/macos_vision_bridge/swift_ocr_bridge.swift` | Swift 源码 |
| `integrations/macos_vision_bridge/swift_ocr_bridge` | 编译产物(107KB) |
| `integrations/macos_vision_bridge/README.md` | 编译步骤(规划中) |

### 编译步骤

```bash
cd integrations/macos_vision_bridge
swiftc -o swift_ocr_bridge swift_ocr_bridge.swift
```

---

## 5. PyObjC 依赖

虽然项目用 Swift bridge 绕过了 PyObjC,但已安装:

| 包 | 版本 | 大小 | 状态 |
|---|---|---|---|
| pyobjc-core | 12.2.1 | ~6.5 MB | 备用 |
| pyobjc-framework-Cocoa | 12.2.1 | ~387 KB | 备用 |
| pyobjc-framework-Quartz | 12.2.1 | ~217 KB | 备用 |
| pyobjc-framework-CoreML | 12.2.1 | ~11 KB | 备用 |
| pyobjc-framework-Vision | 12.2.1 | ~21 KB | 备用 |

**当前项目不直接 import PyObjC**(已移除所有 `from Vision import ...`),保留仅供后续如需用图片识别 / CoreML 时复用。

---

## 6. 后续可优化方向 (非阻塞)

1. **修复 RapidOcrProvider confidence 字段返回** — 当前 0.0000
2. **批量并行** — 当前 PDF 逐页串行,改成多页并行可加速 2-3x
3. **跳过空白页** — 合同常有空白页(白色像素 >95%),跳过可省 ~10-20%
4. **图像预处理** — 仅对低置信度页(<0.95)启用二值化 / 去噪
5. **Swift bridge 长驻进程** — 当前每次启动 swift 进程(启动 ~200ms),改成 daemon 模型可省启动开销

---

## 7. 决策历史

- **v0.4.0 (2026-07-23)**: 优先级链改为 Vision → RapidOCR → EasyOCR → Tesseract;PyObjC 路径废弃(中文乱码 bug);Swift bridge 路径启用
- **v0.3.0 (2026-07-22)**: RapidOcrProvider 加入(从 optional second engine 升级为默认 fallback)
- **v0.2.0**: EasyOCR 为默认引擎(纯 Python,中文支持好)
- **v0.1.0**: Tesseract 为默认引擎
