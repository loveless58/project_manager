#!/usr/bin/env python3
"""55 真实样本 KB 回归测试(2026-07-23 baseline)

覆盖:
- 顶层散落: 8 docx
- 项目投标/招标文件/: 11 docx
- 项目投标/投标文件/: 3 docx
- 项目投标/合同文件/: 6 docx
- 项目投标/报名材料/: 10 docx
- 资金预测/: 7 xlsx
- 付款报备/: 6 xlsx
- 项目明细表.xlsx: 1 xlsx
- 其他: 3 xlsx

已知 KB 边缘案例(2 个,接受):
- [48] 附件4:合同模板.docx: 文件名含"合同"+"模板"(模板信号强),KB 高置信判合同
- [49] 附件1-3.docx: 文件名无信号 + raw_text 为空(纯扫描件),KB 没法判断

这两类场景 LLM fallback 设计目标就是为了接住,但 raw_text 空 LLM 也只能猜。
"""
import os
import sys
from pathlib import Path


from skills.document_parse import parse


SAMPLE_ROOT_ENV = "PROJECT_MANAGER_REAL_SAMPLE_ROOT"


class RealSampleConfigurationError(RuntimeError):
    """Raised when the manual real-sample regression tool is not configured."""


def get_sample_root(environ=None) -> Path:
    environment = os.environ if environ is None else environ
    configured = environment.get(SAMPLE_ROOT_ENV, "").strip()
    if not configured:
        raise RealSampleConfigurationError(
            f"set {SAMPLE_ROOT_ENV} to the directory containing the real samples"
        )
    root = Path(configured).expanduser().resolve()
    if not root.is_dir():
        raise RealSampleConfigurationError(
            f"{SAMPLE_ROOT_ENV} must be an existing directory: {root}"
        )
    return root

SAMPLES = [
    # ===== 顶层散落(已存在 4 个)=====
    ("竞价文件全国中小企业数字化转型服务平台开发项目1.docx", "招标公告"),
    ("20250527-核心网络设备维保维护服务合同2025.docx", "合同文件"),
    ("中国烟草总公司山西省公司网络和安全设备、机房、软硬件平台运维服务项目合同（水印版）.docx", "合同文件"),
    ("服务器区防火墙系统升级采购合同.docx", "合同文件"),
    ("维保合同_文字版.docx", "合同文件"),
    ("（新）已法审-内蒙古农村商业银行股份有限公司呼和浩特中心支行2026-2027年度九楼机房X86等设备维保合同-622.docx", "合同文件"),
    ("核心网络设备维保维护服务合同2026-发华胜-接受所有修订(1)(1).docx", "合同文件"),
    ("【20260715TYL已审1】RPO协议-美偲互动&华胜天成 260709(1).docx", "合同文件"),

    # ===== 项目投标/招标文件/ 目录(新样本)=====
    ("项目文件/项目投标/手持管理终端采购项目/招标文件/招标文件.docx", "招标公告"),
    ("项目文件/项目投标/光学设备状态集约化监控管理系统/招标文件/光学设备状态集约化监控管理系统-公开招标文件.docx", "招标公告"),
    ("项目文件/项目投标/航材院数据管理体系建设（一期）/招标文件/咨询服务项目招标文件--发售版.docx", "招标公告"),
    ("项目文件/项目投标/航材院数据管理体系建设（一期）/招标文件/621招标补充文件.docx", "招标公告"),
    ("项目文件/项目投标/中邮信科2026年新一轮外包人力服务集中采购项目/招标文件/招标文件.docx", "招标公告"),
    ("项目文件/项目投标/某数据中心（二期）装备建设项目包10 Web中间件/招标文件/招标文件某数据中心（二期）装备建设项目（二次招标）10包Web中间件-发售版.docx", "招标公告"),
    ("项目文件/项目投标/运营服务能力验证子系统/招标文件/运营服务能力验证子系统-招标文件20260429.docx", "招标公告"),
    ("项目文件/项目投标/2026年许继电气北京许继企业数字化技术服务集采项目/招标文件/附件2：电气-采购文件.docx", "招标公告"),

    # ===== 项目投标/投标文件/ 目录(新样本)=====
    ("项目文件/项目投标/测试验证数智管理平台云基础设施-GPU服务器（包1）/投标文件/外发文件-GPU服务器（包1）.docx", "投标文件"),
    ("项目文件/项目投标/测试验证数智管理平台云基础设施-GPU服务器（包1）/投标文件/外发文件-GPU服务器（包1）(1).docx", "投标文件"),
    ("项目文件/项目投标/国产化大模型生态高级培训课程/招标文件/投标文件格式.docx", "投标文件"),

    # ===== 项目投标/合同文件/ 目录(新样本)=====
    ("项目文件/项目投标/中国铁塔2026年至2027年IT开发能力服务采购项目-标段一：业务系统代码开发/合同文件/合作协议_华胜&睿嘉_改2_版本2.docx", "合同文件"),
    ("项目文件/项目投标/中国铁塔2026年至2027年IT开发能力服务采购项目-标段一：业务系统代码开发/合同文件/合作协议_华胜&睿嘉_改2_版本1.docx", "合同文件"),
    ("项目文件/项目投标/服务器区防火墙系统升级采购/合同文件/服务器区防火墙系统升级采购合同.docx", "合同文件"),
    ("项目文件/项目投标/测试验证数智管理平台云基础设施-GPU服务器（包1）/合同文件/测试验证数智管理平台云基础设施合同.docx", "合同文件"),
    ("项目文件/项目投标/运营服务能力验证子系统/合同文件/运营服务能力验证子系统技术开发合同0430-IP修订.docx", "合同文件"),

    # ===== 项目投标/报名材料/ 目录(新样本)=====
    ("项目文件/项目投标/光学设备状态集约化监控管理系统/报名材料/报名材料.docx", "报名材料"),
    ("项目文件/项目投标/国产化算力租赁采购项目/报名材料/保密承诺书-国产化算力租赁.docx", "报名材料"),
    ("项目文件/项目投标/国产化算力租赁采购项目/报名材料/授权委托书-国产化算力租赁.docx", "报名材料"),
    ("项目文件/项目投标/紫金矿业入围项目/报名材料/供应商业务授权委托书模板.docx", "报名材料"),
    ("项目文件/项目投标/紫金矿业入围项目/报名材料/特定关系人登记表.docx", "报名材料"),
    ("项目文件/项目投标/紫金矿业入围项目/报名材料/廉洁合作承诺函.docx", "报名材料"),
    ("项目文件/项目投标/任务与服务调度管理系统/报名材料/任务与服务调度管理系统-附件1 授权委托书等.docx", "报名材料"),
    ("项目文件/项目投标/七所报名项目/报名材料/投标人信息登记备案表.docx", "报名材料"),
    ("项目文件/项目投标/中国烟草总公司山西省公司2026-2029年网络和安全设备运维服务/报名材料/授权委托书-山西烟草.docx", "报名材料"),
    ("项目文件/项目投标/中国烟草总公司山西省公司2026-2029年网络和安全设备运维服务/报名材料/购买招标文件所需资料.docx", "报名材料"),

    # ===== 资金预测/ (其他类)=====
    ("资金预测/全订单全周期.xlsx", "其他"),
    ("资金预测/CY 26 资金滚动预测-6-8月-软件外包/CY 26 资金滚动预测-6-8月-软件外包.xlsx", "其他"),
    ("资金预测/CY 26 资金滚动预测-6-8月-软件外包/陈金水-CY 26 资金滚动预测-6-8月-软件外包-cs.xlsx", "其他"),
    ("资金预测/CY 26 资金滚动预测-Q3-软件外包/孙建军-CY 26 资金滚动预测-Q3-软件外包.xlsx", "其他"),
    ("资金预测/CY 26 资金滚动预测-Q3-软件外包/闫昊-CY 26 资金滚动预测-Q3-软件外包(1).xlsx", "其他"),

    # ===== 付款报备/ (其他类 - 报备/报名)=====
    ("付款报备/报备明细/2026年6月17日采购款-软件外包.xlsx", "其他"),
    ("付款报备/报备明细/2026年5月13日采购款报名-软件外包.xlsx", "其他"),
    ("付款报备/报备明细/2026年6月30日采购款-软件外包.xlsx", "其他"),
    ("付款报备/报备明细/2026年7月15日采购款-软件外包.xlsx", "其他"),
    ("付款报备/模板.xlsx", "其他"),
    ("付款报备/2026年7月22日采购款-软件外包.xlsx", "其他"),

    # ===== 项目明细表.xlsx (其他) =====
    ("项目明细表.xlsx", "其他"),
    ("项目文件/项目投标/测试验证数智管理平台云基础设施-GPU服务器（包1）/报名材料/承诺书递交说明及购标说明(含两个承诺书).docx", "报名材料"),

    # ===== 项目投标/招标文件/ 更多样本 =====
    ("项目文件/项目投标/2026年许继电气北京许继企业数字化技术服务集采项目/招标文件/附件4：合同模板.docx", "招标公告"),
    ("项目文件/项目投标/某数据中心（二期）装备建设项目包10 Web中间件/招标文件/附件1-3.docx", "招标公告"),
    ("项目文件/项目投标/运营服务能力验证子系统/招标文件/运营服务能力验证子系统研制要求-招标.docx", "招标公告"),
    ("项目文件/项目投标/运营服务能力验证子系统/招标文件/2640STC21687_澄清1号.docx", "招标公告"),
    ("项目文件/项目投标/国网信通产业集团2026年产业单位第六次服务类公开谈判采购（二）/招标文件/CY8526JFF06-常规5.21采购文件.docx", "招标公告"),

    # ===== 项目投标/合同文件/ 补样本 =====
    ("项目文件/项目投标/中国铁塔2026年至2027年IT开发能力服务采购项目-标段一：业务系统代码开发/合同文件/合作协议_华胜&睿嘉20260601(1).docx", "合同文件"),

    # ===== 资金预测 补样本 =====
    ("资金预测/CY 26 资金滚动预测-Q3-软件外包/陈金水-CY 26 资金滚动预测-Q3-软件外包.xlsx", "其他"),
    ("资金预测/CY 26 资金滚动预测-Q3-软件外包/雷能-CY 26 资金滚动预测-Q3-软件外包.xlsx", "其他"),
]


def missing_sample_files(sample_root: Path):
    return [
        (relative_path, expected)
        for relative_path, expected in SAMPLES
        if not (sample_root / relative_path).is_file()
    ]


def main() -> int:
    try:
        sample_root = get_sample_root()
    except RealSampleConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    missing = missing_sample_files(sample_root)
    if missing:
        print(f"Missing sample files: {len(missing)}/{len(SAMPLES)}", file=sys.stderr)
        for relative_path, _ in missing:
            print(f"  - {relative_path}", file=sys.stderr)
        return 1

    hits = 0
    failures = []
    print(f"Running {len(SAMPLES)} real samples from {sample_root}")
    for index, (relative_path, expected) in enumerate(SAMPLES, 1):
        source_path = sample_root / relative_path
        try:
            result = parse(str(source_path))
            actual = result["business_judgement"]["category"]
            if actual == expected:
                hits += 1
                print(f"{index:>3} PASS {relative_path}")
            else:
                failures.append((relative_path, expected, actual))
                print(f"{index:>3} FAIL {relative_path}: expected={expected}, actual={actual}")
        except Exception as exc:
            failures.append((relative_path, expected, f"EXCEPTION: {exc}"))
            print(f"{index:>3} ERROR {relative_path}: {exc}")

    print(f"Real-sample result: {hits}/{len(SAMPLES)} passed")
    if failures:
        print(f"Failures: {len(failures)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())