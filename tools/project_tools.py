"""
项目生命周期工具层
实现：ProjectTools, ArchiveTools, ReportTools, MigrateTools
"""

import os
import re
import json
import shutil
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

from common.workspace_config import default_business_root, default_project_files_dir

# ============= 常量配置 =============

# Legacy workspace paths are resolved only when a project tool is instantiated.
PHASES = ["项目投标", "项目弃标", "项目丢标", "项目执行"]

ARCHIVE_SUBDIRS = {
    "招标": "招标文件",
    "公告": "招标文件",
    "文件": "招标文件",
    "报名": "报名材料",
    "投标": "投标文件",
    "合同": "合同文件",
    "变更": "变更记录",
    "变更记录": "变更记录",
    "中标": "中标文件",
    "验收": "验收文档",
    "交付": "验收文档",
}


# ============= ProjectTools =============

class ProjectTools:
    """项目扫描与读取工具"""
    
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or default_project_files_dir()
        self.index_path = os.path.join(self.base_dir, "index.json")
    
    def scan_projects(self, phase: Optional[str] = None) -> Dict:
        """扫描三阶段目录，返回项目列表"""
        phases_to_scan = [phase] if phase and phase in PHASES else PHASES
        
        projects = []
        phase_counts = {p: 0 for p in PHASES}
        
        for ph in phases_to_scan:
            phase_dir = os.path.join(self.base_dir, ph)
            if not os.path.exists(phase_dir):
                continue
            
            for name in os.listdir(phase_dir):
                project_path = os.path.join(phase_dir, name)
                if not os.path.isdir(project_path) or name.startswith("."):
                    continue
                
                record_path = os.path.join(project_path, "项目记录.md")
                has_record = os.path.exists(record_path)
                
                projects.append({
                    "name": name,
                    "phase": ph,
                    "path": os.path.join(ph, name),
                    "has_record": has_record,
                })
                phase_counts[ph] += 1
        
        return {
            "projects": projects,
            "count": len(projects),
            "phase_counts": phase_counts,
        }
    
    def read_project_record(self, project_name: str) -> Dict:
        """读取指定项目的项目记录.md"""
        # 查找项目目录
        project_path = self._find_project_path(project_name)
        if not project_path:
            return {"error": f"Project '{project_name}' not found"}
        
        record_path = os.path.join(project_path, "项目记录.md")
        if not os.path.exists(record_path):
            return {"error": f"项目记录.md not found for '{project_name}'"}
        
        with open(record_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        return self._parse_record(content)
    
    def _find_project_path(self, project_name: str) -> Optional[str]:
        """在三阶段目录中查找项目路径"""
        for phase in PHASES:
            phase_dir = os.path.join(self.base_dir, phase)
            if not os.path.exists(phase_dir):
                continue
            for name in os.listdir(phase_dir):
                if name == project_name or project_name in name:
                    return os.path.join(phase_dir, name)
        return None
    
    def _parse_record(self, content: str) -> Dict:
        """解析项目记录.md 为结构化数据"""
        result = {
            "basic_info": {},
            "timeline": {},
            "status": {},
            "milestones": [],
            "tasks": {"completed": 0, "total": 0},
            "deliverables": {"completed": 0, "total": 0},
            "weekly_reports": [],
            "risks": [],
            "key_files": [],
            "todos": [],
        }
        
        # 解析基本信息
        basic_match = re.search(r"## 基本信息\s*(.*?)(?=## |$)", content, re.DOTALL)
        if basic_match:
            for line in basic_match.group(1).strip().split("\n"):
                match = re.match(r"(?:-\s*)?\*\*(.+?)\*\*[:：]\s*(.+)", line.strip())
                if match:
                    result["basic_info"][match.group(1)] = match.group(2).strip()
        
        # 解析时间节点
        timeline_match = re.search(r"## 时间节点\s*(.*?)(?=## |$)", content, re.DOTALL)
        if timeline_match:
            for line in timeline_match.group(1).strip().split("\n"):
                match = re.match(r"(?:-\s*)?\*\*(.+?)\*\*[:：]\s*(.+)", line.strip())
                if match:
                    result["timeline"][match.group(1)] = match.group(2).strip()
        
        # 解析项目状态
        status_match = re.search(r"## 项目状态\s*(.*?)(?=## |$)", content, re.DOTALL)
        if status_match:
            for line in status_match.group(1).strip().split("\n"):
                match = re.match(r"-\s*(.+?)[:：]\s*(.+)", line.strip())
                if match:
                    result["status"][match.group(1)] = match.group(2).strip()
        
        # 解析里程碑表格（更健壮的正则）
        milestone_section = re.search(r"## 里程碑管理\s*(.*?)(?=## |$)", content, re.DOTALL)
        if milestone_section:
            section_text = milestone_section.group(1).strip()
            # 找到表格行（以 | 开头）
            lines = section_text.split('\n')
            in_table = False
            for line in lines:
                line = line.strip()
                if line.startswith('|') and not line.startswith('|---'):
                    cells = [c.strip() for c in line.strip('|').split('|')]
                    if len(cells) >= 3 and cells[0] != '里程碑':  # 跳过表头
                        result["milestones"].append({
                            "name": cells[0],
                            "deadline": cells[1] if len(cells) > 1 else "",
                            "status": cells[2] if len(cells) > 2 else "",
                            "completed_date": cells[3] if len(cells) > 3 else "",
                            "note": cells[4] if len(cells) > 4 else "",
                        })
        
        # 解析任务跟踪（按区块）
        task_section = re.search(r"## 任务跟踪\s*(.*?)(?=## |$)", content, re.DOTALL)
        if task_section:
            tasks = re.findall(r"- \[([ x])\] (.+)", task_section.group(1))
            result["tasks"] = {
                "completed": sum(1 for t in tasks if t[0] == "x"),
                "total": len(tasks),
                "items": [t[1] for t in tasks]
            }
        
        # 解析交付物清单（按区块）
        deliverable_section = re.search(r"## 交付物清单\s*(.*?)(?=## |$)", content, re.DOTALL)
        if deliverable_section:
            dels = re.findall(r"- \[([ x])\] (.+)", deliverable_section.group(1))
            result["deliverables"] = {
                "completed": sum(1 for d in dels if d[0] == "x"),
                "total": len(dels),
                "items": [d[1] for d in dels]
            }
        
        # 解析风险与问题
        risk_section = re.search(r"## 风险与问题\s*(.*?)(?=## |$)", content, re.DOTALL)
        if risk_section:
            risks = re.findall(r"- ([🔴🟡🟢⚪])\s*(.+)", risk_section.group(1))
            result["risks"] = [{"level": r[0], "desc": r[1]} for r in risks]
        
        # 解析周报记录
        weekly_section = re.search(r"## 周报记录\s*(.*?)(?=## |$)", content, re.DOTALL)
        if weekly_section:
            reports = re.findall(r"- (\d{4}-\d{2}-\d{2}):\s*(.+)", weekly_section.group(1))
            result["weekly_reports"] = [{"date": r[0], "content": r[1]} for r in reports]
        
        # 解析关键文件
        file_section = re.search(r"## 关键文件\s*(.*?)(?=## |$)", content, re.DOTALL)
        if file_section:
            files = re.findall(r"- `(.+?)`[—\-]\s*(.+)", file_section.group(1))
            result["key_files"] = [{"path": f[0], "desc": f[1]} for f in files]
        
        # 解析待办事项
        todo_section = re.search(r"## 待办事项\s*(.*?)(?=## |$)", content, re.DOTALL)
        if todo_section:
            todos = re.findall(r"- \[([ x])\] (.+)", todo_section.group(1))
            result["todos"] = {
                "completed": sum(1 for t in todos if t[0] == "x"),
                "total": len(todos),
                "items": [t[1] for t in todos]
            }
        
        # 查找下一个里程碑
        for m in result["milestones"]:
            if m["status"] not in ["已完成", "已完成 "]:
                result["next_milestone"] = m["name"]
                result["next_deadline"] = m["deadline"]
                break
        
        return result
    
    def check_milestones(self, project_name: Optional[str] = None, days: int = 7) -> Dict:
        """检查里程碑状态，识别逾期和即将到期"""
        today = datetime.now().date()
        
        if project_name:
            record = self.read_project_record(project_name)
            if "error" in record:
                return record
            projects = [{"name": project_name, "record": record}]
        else:
            # 扫描所有项目
            scan = self.scan_projects()
            projects = []
            for p in scan["projects"]:
                record = self.read_project_record(p["name"])
                if "error" not in record:
                    projects.append({"name": p["name"], "record": record})
        
        overdue = []
        upcoming = []
        normal = []
        
        for proj in projects:
            record = proj["record"]
            for m in record.get("milestones", []):
                if not m["deadline"] or m["deadline"] == "-":
                    continue
                
                try:
                    deadline = datetime.strptime(m["deadline"], "%Y-%m-%d").date()
                except ValueError:
                    continue
                
                days_remaining = (deadline - today).days
                
                item = {
                    "project": proj["name"],
                    "milestone": m["name"],
                    "deadline": m["deadline"],
                    "days_remaining": days_remaining,
                }
                
                if days_remaining < 0:
                    item["days_overdue"] = -days_remaining
                    overdue.append(item)
                elif days_remaining <= days:
                    upcoming.append(item)
                else:
                    normal.append(item)
        
        # 排序
        overdue.sort(key=lambda x: x["days_overdue"], reverse=True)
        upcoming.sort(key=lambda x: x["days_remaining"])
        
        return {
            "overdue": overdue,
            "upcoming": upcoming,
            "normal": normal,
            "summary": {
                "overdue_count": len(overdue),
                "upcoming_count": len(upcoming),
                "total_checked": len(projects),
            }
        }
    
    def check_deliverables(self, project_name: str) -> Dict:
        """扫描项目子目录，与交付物清单对比"""
        project_path = self._find_project_path(project_name)
        if not project_path:
            return {"error": f"Project '{project_name}' not found"}
        
        record = self.read_project_record(project_name)
        if "error" in record:
            return record
        
        # 扫描实际文件
        actual_files = []
        for subdir in os.listdir(project_path):
            subdir_path = os.path.join(project_path, subdir)
            if os.path.isdir(subdir_path) and not subdir.startswith("."):
                for file in os.listdir(subdir_path):
                    actual_files.append(os.path.join(subdir, file))
        
        # 交付物清单（从项目记录解析）
        deliverables = record.get("deliverables", {"completed": 0, "total": 0})
        
        return {
            "project": project_name,
            "actual_files": actual_files,
            "deliverable_count": deliverables.get("total", 0),
            "completed_count": deliverables.get("completed", 0),
            "file_count": len(actual_files),
        }
    
    def write_response(self, content: str, filename: str) -> str:
        """将内容写入 state/ 目录"""
        state_dir = os.path.join(os.path.dirname(__file__), "..", "state")
        os.makedirs(state_dir, exist_ok=True)
        
        filepath = os.path.join(state_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        
        return f"Saved to state/{filename}"


# ============= ArchiveTools =============

class ArchiveTools:
    """智能归档工具"""
    
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or default_project_files_dir()
    
    def archive_files(self, source_dir: Optional[str] = None) -> Dict:
        """智能归档散落文件"""
        source = source_dir or default_business_root()
        
        # 扫描文件
        files = []
        for f in os.listdir(source):
            fpath = os.path.join(source, f)
            if os.path.isfile(fpath) and not f.startswith("."):
                files.append(f)
        
        # 获取项目列表
        projects = self._get_project_names()
        
        archived = 0
        failed = 0
        unmatched = []
        
        for filename in files:
            matched_project = self._match_project(filename, projects)
            if matched_project:
                subdir = self._classify_file(filename)
                dest = os.path.join(self.base_dir, matched_project["phase"], 
                                   matched_project["name"], subdir)
                
                try:
                    os.makedirs(dest, exist_ok=True)
                    shutil.copy2(os.path.join(source, filename), 
                                os.path.join(dest, filename))
                    os.remove(os.path.join(source, filename))
                    archived += 1
                except Exception as e:
                    failed += 1
            else:
                unmatched.append(filename)
        
        return {
            "scanned": len(files),
            "archived": archived,
            "failed": failed,
            "unmatched": unmatched,
            "migrations": self._detect_migrations(),
        }
    
    def _get_project_names(self) -> List[Dict]:
        """获取所有项目名称和阶段"""
        projects = []
        for phase in PHASES:
            phase_dir = os.path.join(self.base_dir, phase)
            if not os.path.exists(phase_dir):
                continue
            for name in os.listdir(phase_dir):
                if os.path.isdir(os.path.join(phase_dir, name)) and not name.startswith("."):
                    projects.append({"name": name, "phase": phase})
        return projects
    
    def _match_project(self, filename: str, projects: List[Dict]) -> Optional[Dict]:
        """根据文件名匹配项目"""
        for p in projects:
            # 简单匹配：文件名包含项目名称关键词
            keywords = p["name"].replace("项目", "").replace("采购", "").replace("服务", "").split()
            for kw in keywords:
                if len(kw) > 2 and kw in filename:
                    return p
        return None
    
    def _classify_file(self, filename: str) -> str:
        """根据文件名分类到子目录"""
        lower = filename.lower()
        for keyword, subdir in ARCHIVE_SUBDIRS.items():
            if keyword in lower:
                return subdir
        return "其他文件"
    
    def _detect_migrations(self) -> List[str]:
        """检测可能需要迁移的项目"""
        migrations = []
        # 检查项目投标目录是否有中标文件
        bid_dir = os.path.join(self.base_dir, "项目投标")
        if os.path.exists(bid_dir):
            for name in os.listdir(bid_dir):
                win_dir = os.path.join(bid_dir, name, "中标文件")
                if os.path.exists(win_dir) and os.listdir(win_dir):
                    migrations.append(f"建议迁移: {name} -> 项目执行（已中标）")

        return migrations


# ============= ReportTools =============

class ReportTools:
    """报告生成工具"""
    
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or default_project_files_dir()
        self.project_tools = ProjectTools(self.base_dir)
    
    def generate_bid_overview(self, output_file: Optional[str] = None) -> str:
        """生成投标进度总览"""
        projects = self.project_tools.scan_projects(phase="项目投标")
        
        today = datetime.now().strftime("%Y-%m-%d")
        lines = [
            f"# 投标进度总览",
            f"",
            f"> **生成日期**: {today}",
            f"> **投标项目数**: {projects['count']}",
            f"",
            "---",
            "",
            "## 项目列表",
            "",
            "| 项目名称 | 状态 | 下一里程碑 | 剩余天数 | 风险 |",
            "|----------|------|------------|----------|------|",
        ]
        
        for p in projects["projects"]:
            record = self.project_tools.read_project_record(p["name"])
            next_ms = record.get("next_milestone", "-")
            next_deadline = record.get("next_deadline", "")
            
            days_remaining = "-"
            if next_deadline:
                try:
                    d = datetime.strptime(next_deadline, "%Y-%m-%d").date()
                    days = (d - datetime.now().date()).days
                    days_remaining = f"{days} 天"
                except:
                    pass
            
            risk = "🟢"
            if days_remaining != "-" and isinstance(days_remaining, str):
                try:
                    d = int(days_remaining.split()[0])
                    if d < 0:
                        risk = "🔴"
                    elif d <= 3:
                        risk = "🟡"
                except:
                    pass
            
            lines.append(f"| {p['name']} | 投标中 | {next_ms} | {days_remaining} | {risk} |")
        
        content = "\n".join(lines)
        
        # 保存
        output = output_file or os.path.join(self.base_dir, "投标进度总览.md")
        with open(output, "w", encoding="utf-8") as f:
            f.write(content)
        
        return f"投标进度总览已生成: {output}"
    
    def generate_project_overview(self, phase: Optional[str] = None) -> str:
        """生成全局项目状态概览"""
        projects = self.project_tools.scan_projects(phase=phase)
        
        today = datetime.now().strftime("%Y-%m-%d")
        lines = [
            f"# 项目总览",
            f"",
            f"> **生成日期**: {today}",
            f"> **项目总数**: {projects['count']}（投标 {projects['phase_counts'].get('项目投标', 0)} / 弃标 {projects['phase_counts'].get('项目弃标', 0)} / 丢标 {projects['phase_counts'].get('项目丢标', 0)} / 执行 {projects['phase_counts'].get('项目执行', 0)}）",
            f"",
            "---",
            "",
            "## 项目速查表",
            "",
            "| 项目名称 | 阶段 | 下一里程碑 | 剩余 | 风险 | 进度 |",
            "|----------|------|------------|------|------|------|",
        ]
        
        for p in projects["projects"]:
            record = self.project_tools.read_project_record(p["name"])
            next_ms = record.get("next_milestone", "-")
            next_deadline = record.get("next_deadline", "")
            
            days = "-"
            risk = "🟢"
            if next_deadline:
                try:
                    d = datetime.strptime(next_deadline, "%Y-%m-%d").date()
                    remaining = (d - datetime.now().date()).days
                    days = f"{remaining} 天"
                    if remaining < 0:
                        risk = "🔴"
                    elif remaining <= 7:
                        risk = "🟡"
                except:
                    pass
            
            milestones = record.get("milestones", [])
            completed = sum(1 for m in milestones if m.get("status") in ["已完成", "已完成 "])
            total = len(milestones)
            progress = f"{completed}/{total}"
            
            lines.append(f"| {p['name']} | {p['phase']} | {next_ms} | {days} | {risk} | {progress} |")
        
        content = "\n".join(lines)
        
        output = os.path.join(self.base_dir, "项目总览.md")
        with open(output, "w", encoding="utf-8") as f:
            f.write(content)
        
        return f"项目总览已生成: {output}"
    
    def generate_report(self, project_name: str, report_type: str = "weekly") -> str:
        """生成单个项目报告"""
        record = self.project_tools.read_project_record(project_name)
        if "error" in record:
            return record["error"]
        
        today = datetime.now().strftime("%Y-%m-%d")
        
        if report_type == "weekly":
            lines = [
                f"# {project_name} - 周报",
                f"",
                f"**日期**: {today}",
                f"",
                "## 本周进展",
            ]
            for r in record.get("weekly_reports", [])[-5:]:
                lines.append(f"- {r['date']}: {r['content']}")
            
            lines.extend([
                "",
                "## 里程碑状态",
            ])
            for m in record.get("milestones", []):
                status = "✅" if m["status"] in ["已完成", "已完成 "] else "⏳"
                lines.append(f"- {status} {m['name']} (截止: {m['deadline']}) - {m['status']}")
            
            lines.extend([
                "",
                "## 风险与问题",
            ])
            for r in record.get("risks", []):
                lines.append(f"- {r['level']} {r['desc']}")
            
        elif report_type == "brief":
            lines = [
                f"# {project_name} - 简报",
                f"",
                f"**日期**: {today}",
                f"",
                f"- 客户: {record.get('basic_info', {}).get('客户', 'N/A')}",
                f"- 金额: {record.get('basic_info', {}).get('金额', 'N/A')}",
                f"- 下一里程碑: {record.get('next_milestone', 'N/A')}",
                f"- 里程碑完成: {sum(1 for m in record.get('milestones', []) if m.get('status') in ['已完成', '已完成 '])}/{len(record.get('milestones', []))}",
            ]
        
        else:  # status
            lines = [
                f"# {project_name} - 状态报告",
                f"",
                f"**日期**: {today}",
                f"",
                "## 基本信息",
            ]
            for k, v in record.get("basic_info", {}).items():
                lines.append(f"- {k}: {v}")
            
            lines.extend(["", "## 里程碑", ""])
            for m in record.get("milestones", []):
                lines.append(f"| {m['name']} | {m['deadline']} | {m['status']} | {m.get('note', '')} |")
        
        return "\n".join(lines)


# ============= MigrateTools =============

class MigrateTools:
    """状态迁移工具"""
    
    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or default_project_files_dir()
        self.project_tools = ProjectTools(self.base_dir)
    
    def migrate_project(self, project_name: str, 
                       to_phase: Optional[str] = None,
                       update_status: Optional[str] = None) -> Dict:
        """迁移项目或更新状态"""
        # 查找当前位置
        current_phase = None
        current_path = None
        for phase in PHASES:
            phase_dir = os.path.join(self.base_dir, phase)
            if not os.path.exists(phase_dir):
                continue
            for name in os.listdir(phase_dir):
                if name == project_name or project_name in name:
                    current_phase = phase
                    current_path = os.path.join(phase_dir, name)
                    break
            if current_path:
                break
        
        if not current_path:
            return {"error": f"Project '{project_name}' not found"}
        
        if to_phase:
            if to_phase not in PHASES:
                return {"error": f"Invalid phase: {to_phase}. Must be one of {PHASES}"}
            
            if to_phase == current_phase:
                return {
                    "action": "no_change",
                    "message": f"Project already in {current_phase}",
                }
            
            # 执行迁移
            dest_dir = os.path.join(self.base_dir, to_phase)
            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, os.path.basename(current_path))
            
            try:
                shutil.move(current_path, dest_path)
                return {
                    "action": "migrated",
                    "from_phase": current_phase,
                    "to_phase": to_phase,
                    "message": f"Project migrated from {current_phase} to {to_phase}",
                }
            except Exception as e:
                return {"error": f"Migration failed: {str(e)}"}
        
        if update_status:
            # 仅更新状态（更新项目记录.md）
            record_path = os.path.join(current_path, "项目记录.md")
            if os.path.exists(record_path):
                with open(record_path, "r", encoding="utf-8") as f:
                    content = f.read()
                
                # 简单替换状态行
                content = re.sub(
                    r"\*\*项目状态\*\*: .+",
                    f"**项目状态**: {update_status}",
                    content
                )
                
                with open(record_path, "w", encoding="utf-8") as f:
                    f.write(content)
                
                return {
                    "action": "updated",
                    "message": f"Status updated to: {update_status}",
                }
            else:
                return {"error": "项目记录.md not found"}
        
        return {
            "action": "checked",
            "current_phase": current_phase,
            "message": f"Project is currently in {current_phase}",
        }
