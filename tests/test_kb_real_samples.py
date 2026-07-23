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
sys.path.insert(0, '/Users/zhang/Desktop/工作文件/project_manager')

from skills.document_parse import parse

# 50+ 真实样本清单(覆盖 5 个硬编码目录 + 顶层散落 + 多个项目子目录)
SAMPLES = [
    # ===== 顶层散落(已存在 4 个)=====
    ("/Users/zhang/Desktop/工作文件/竞价文件全国中小企业数字化转型服务平台开发项目1.docx", "招标公告"),
    ("/Users/zhang/Desktop/工作文件/20250527-核心网络设备维保维护服务合同2025.docx", "合同文件"),
    ("/Users/zhang/Desktop/工作文件/中国烟草总公司山西省公司网络和安全设备、机房、软硬件平台运维服务项目合同（水印版）.docx", "合同文件"),
    ("/Users/zhang/Desktop/工作文件/服务器区防火墙系统升级采购合同.docx", "合同文件"),
    ("/Users/zhang/Desktop/工作文件/维保合同_文字版.docx", "合同文件"),
    ("/Users/zhang/Desktop/工作文件/（新）已法审-内蒙古农村商业银行股份有限公司呼和浩特中心支行2026-2027年度九楼机房X86等设备维保合同-622.docx", "合同文件"),
    ("/Users/zhang/Desktop/工作文件/核心网络设备维保维护服务合同2026-发华胜-接受所有修订(1)(1).docx", "合同文件"),
    ("/Users/zhang/Desktop/工作文件/【20260715TYL已审1】RPO协议-美偲互动&华胜天成 260709(1).docx", "合同文件"),

    # ===== 项目投标/招标文件/ 目录(新样本)=====
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/手持管理终端采购项目/招标文件/招标文件.docx", "招标公告"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/光学设备状态集约化监控管理系统/招标文件/光学设备状态集约化监控管理系统-公开招标文件.docx", "招标公告"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/航材院数据管理体系建设（一期）/招标文件/咨询服务项目招标文件--发售版.docx", "招标公告"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/航材院数据管理体系建设（一期）/招标文件/621招标补充文件.docx", "招标公告"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/中邮信科2026年新一轮外包人力服务集中采购项目/招标文件/招标文件.docx", "招标公告"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/某数据中心（二期）装备建设项目包10 Web中间件/招标文件/招标文件某数据中心（二期）装备建设项目（二次招标）10包Web中间件-发售版.docx", "招标公告"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/运营服务能力验证子系统/招标文件/运营服务能力验证子系统-招标文件20260429.docx", "招标公告"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/2026年许继电气北京许继企业数字化技术服务集采项目/招标文件/附件2：电气-采购文件.docx", "招标公告"),

    # ===== 项目投标/投标文件/ 目录(新样本)=====
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/测试验证数智管理平台云基础设施-GPU服务器（包1）/投标文件/外发文件-GPU服务器（包1）.docx", "投标文件"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/测试验证数智管理平台云基础设施-GPU服务器（包1）/投标文件/外发文件-GPU服务器（包1）(1).docx", "投标文件"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/国产化大模型生态高级培训课程/招标文件/投标文件格式.docx", "投标文件"),

    # ===== 项目投标/合同文件/ 目录(新样本)=====
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/中国铁塔2026年至2027年IT开发能力服务采购项目-标段一：业务系统代码开发/合同文件/合作协议_华胜&睿嘉_改2_版本2.docx", "合同文件"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/中国铁塔2026年至2027年IT开发能力服务采购项目-标段一：业务系统代码开发/合同文件/合作协议_华胜&睿嘉_改2_版本1.docx", "合同文件"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/服务器区防火墙系统升级采购/合同文件/服务器区防火墙系统升级采购合同.docx", "合同文件"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/测试验证数智管理平台云基础设施-GPU服务器（包1）/合同文件/测试验证数智管理平台云基础设施合同.docx", "合同文件"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/运营服务能力验证子系统/合同文件/运营服务能力验证子系统技术开发合同0430-IP修订.docx", "合同文件"),

    # ===== 项目投标/报名材料/ 目录(新样本)=====
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/光学设备状态集约化监控管理系统/报名材料/报名材料.docx", "报名材料"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/国产化算力租赁采购项目/报名材料/保密承诺书-国产化算力租赁.docx", "报名材料"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/国产化算力租赁采购项目/报名材料/授权委托书-国产化算力租赁.docx", "报名材料"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/紫金矿业入围项目/报名材料/供应商业务授权委托书模板.docx", "报名材料"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/紫金矿业入围项目/报名材料/特定关系人登记表.docx", "报名材料"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/紫金矿业入围项目/报名材料/廉洁合作承诺函.docx", "报名材料"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/任务与服务调度管理系统/报名材料/任务与服务调度管理系统-附件1 授权委托书等.docx", "报名材料"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/七所报名项目/报名材料/投标人信息登记备案表.docx", "报名材料"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/中国烟草总公司山西省公司2026-2029年网络和安全设备运维服务/报名材料/授权委托书-山西烟草.docx", "报名材料"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/中国烟草总公司山西省公司2026-2029年网络和安全设备运维服务/报名材料/购买招标文件所需资料.docx", "报名材料"),

    # ===== 资金预测/ (其他类)=====
    ("/Users/zhang/Desktop/工作文件/资金预测/全订单全周期.xlsx", "其他"),
    ("/Users/zhang/Desktop/工作文件/资金预测/CY 26 资金滚动预测-6-8月-软件外包/CY 26 资金滚动预测-6-8月-软件外包.xlsx", "其他"),
    ("/Users/zhang/Desktop/工作文件/资金预测/CY 26 资金滚动预测-6-8月-软件外包/陈金水-CY 26 资金滚动预测-6-8月-软件外包-cs.xlsx", "其他"),
    ("/Users/zhang/Desktop/工作文件/资金预测/CY 26 资金滚动预测-Q3-软件外包/孙建军-CY 26 资金滚动预测-Q3-软件外包.xlsx", "其他"),
    ("/Users/zhang/Desktop/工作文件/资金预测/CY 26 资金滚动预测-Q3-软件外包/闫昊-CY 26 资金滚动预测-Q3-软件外包(1).xlsx", "其他"),

    # ===== 付款报备/ (其他类 - 报备/报名)=====
    ("/Users/zhang/Desktop/工作文件/付款报备/报备明细/2026年6月17日采购款-软件外包.xlsx", "其他"),
    ("/Users/zhang/Desktop/工作文件/付款报备/报备明细/2026年5月13日采购款报名-软件外包.xlsx", "其他"),
    ("/Users/zhang/Desktop/工作文件/付款报备/报备明细/2026年6月30日采购款-软件外包.xlsx", "其他"),
    ("/Users/zhang/Desktop/工作文件/付款报备/报备明细/2026年7月15日采购款-软件外包.xlsx", "其他"),
    ("/Users/zhang/Desktop/工作文件/付款报备/模板.xlsx", "其他"),
    ("/Users/zhang/Desktop/工作文件/付款报备/2026年7月22日采购款-软件外包.xlsx", "其他"),

    # ===== 项目明细表.xlsx (其他) =====
    ("/Users/zhang/Desktop/工作文件/项目明细表.xlsx", "其他"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/测试验证数智管理平台云基础设施-GPU服务器（包1）/报名材料/承诺书递交说明及购标说明(含两个承诺书).docx", "报名材料"),

    # ===== 项目投标/招标文件/ 更多样本 =====
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/2026年许继电气北京许继企业数字化技术服务集采项目/招标文件/附件4：合同模板.docx", "招标公告"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/某数据中心（二期）装备建设项目包10 Web中间件/招标文件/附件1-3.docx", "招标公告"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/运营服务能力验证子系统/招标文件/运营服务能力验证子系统研制要求-招标.docx", "招标公告"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/运营服务能力验证子系统/招标文件/2640STC21687_澄清1号.docx", "招标公告"),
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/国网信通产业集团2026年产业单位第六次服务类公开谈判采购（二）/招标文件/CY8526JFF06-常规5.21采购文件.docx", "招标公告"),

    # ===== 项目投标/合同文件/ 补样本 =====
    ("/Users/zhang/Desktop/工作文件/项目文件/项目投标/中国铁塔2026年至2027年IT开发能力服务采购项目-标段一：业务系统代码开发/合同文件/合作协议_华胜&睿嘉20260601(1).docx", "合同文件"),

    # ===== 资金预测 补样本 =====
    ("/Users/zhang/Desktop/工作文件/资金预测/CY 26 资金滚动预测-Q3-软件外包/陈金水-CY 26 资金滚动预测-Q3-软件外包.xlsx", "其他"),
    ("/Users/zhang/Desktop/工作文件/资金预测/CY 26 资金滚动预测-Q3-软件外包/雷能-CY 26 资金滚动预测-Q3-软件外包.xlsx", "其他"),
]


def main():
    hits = 0
    misses = []
    not_found = []
    print(f"{'#':<3} {'期望':<8} {'parse':<8} {'置信':<6} {'rule':<15}  状态  文件名")
    print("-" * 110)

    for i, (path, expected) in enumerate(SAMPLES, 1):
        if not os.path.exists(path):
            not_found.append((i, expected, path))
            print(f"{i:<3} {'-':<8} {'-':<8} {'-':<6} {'-':<15}  SKIP  {os.path.basename(path)[:50]}")
            continue

        try:
            r = parse(path)
            actual = r['business_judgement']['category']
            confidence = r['business_judgement']['confidence']
            rule = r['business_judgement'].get('rule_source', '?')
            hit = (actual == expected)
            if hit:
                hits += 1
            else:
                misses.append((i, expected, actual, rule, confidence, path))
            mark = '✓' if hit else '✗'
            fname = os.path.basename(path)[:55]
            print(f"{i:<3} {expected:<8} {actual:<8} {confidence:<6} {rule:<15}  {mark:<5} {fname}")
        except Exception as e:
            print(f"{i:<3} {expected:<8} {'ERR':<8} {'-':<6} {'-':<15}  EXC   {os.path.basename(path)[:50]}  ({e})")

    total = len(SAMPLES)
    tested = total - len(not_found)
    print(f"\n{'=' * 80}")
    print(f"测试样本: {total} 个(实际跑 {tested} 个)")
    print(f"命中: {hits}/{tested} = {hits*100//tested if tested else 0}%")
    if not_found:
        print(f"文件不存在: {len(not_found)} 个")
    if misses:
        print(f"\n未命中详情 ({len(misses)} 个):")
        for i, expected, actual, rule, conf, path in misses:
            print(f"  [{i}] 期望={expected} 实际={actual} 置信={conf} rule={rule}")
            print(f"      {os.path.basename(path)}")


if __name__ == '__main__':
    main()
