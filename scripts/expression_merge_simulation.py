"""
模拟 Expression 合并过程

用法:
    python scripts/expression_merge_simulation.py
    或指定 chat_id:
    python scripts/expression_merge_simulation.py --chat-id <chat_id>
    或指定相似度阈值:
    python scripts/expression_merge_simulation.py --similarity-threshold 0.8
"""

import sys
import os
import json
import argparse
import asyncio
import random
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
from datetime import datetime

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Import after setting up path (required for project imports)
from src.common.database.database_model import Expression, ChatStreams  # noqa: E402
from src.bw_learner.learner_utils import calculate_style_similarity  # noqa: E402
from src.llm_models.utils_model import LLMRequest  # noqa: E402
from src.config.config import model_config  # noqa: E402


def get_chat_name(chat_id: str) -> str:
    """根据 chat_id 获取聊天名称"""
    try:
        chat_stream = ChatStreams.get_or_none(ChatStreams.stream_id == chat_id)
        if chat_stream is None:
            return f"未知聊天 ({chat_id[:8]}...)"
        
        if chat_stream.group_name:
            return f"{chat_stream.group_name}"
        elif chat_stream.user_nickname:
            return f"{chat_stream.user_nickname}的私聊"
        else:
            return f"未知聊天 ({chat_id[:8]}...)"
    except Exception:
        return f"查询失败 ({chat_id[:8]}...)"


def parse_content_list(stored_list: Optional[str]) -> List[str]:
    """解析 content_list JSON 字符串为列表"""
    if not stored_list:
        return []
    try:
        data = json.loads(stored_list)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in data if isinstance(item, str)] if isinstance(data, list) else []


def parse_style_list(stored_list: Optional[str]) -> List[str]:
    """解析 style_list JSON 字符串为列表"""
    if not stored_list:
        return []
    try:
        data = json.loads(stored_list)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in data if isinstance(item, str)] if isinstance(data, list) else []


def find_exact_style_match(
    expressions: List[Expression],
    target_style: str,
    chat_id: str,
    exclude_ids: set
) -> Optional[Expression]:
    """
    查找具有完全匹配 style 的 Expression 记录
    检查 style 字段和 style_list 中的每一项
    """
    for expr in expressions:
        if expr.chat_id != chat_id or expr.id in exclude_ids:
            continue
        
        # 检查 style 字段
        if expr.style == target_style:
            return expr
        
        # 检查 style_list 中的每一项
        style_list = parse_style_list(expr.style_list)
        if target_style in style_list:
            return expr
    
    return None


def find_similar_style_expression(
    expressions: List[Expression],
    target_style: str,
    chat_id: str,
    similarity_threshold: float,
    exclude_ids: set
) -> Optional[Tuple[Expression, float]]:
    """
    查找具有相似 style 的 Expression 记录
    检查 style 字段和 style_list 中的每一项
    
    Returns:
        (Expression, similarity) 或 None
    """
    best_match = None
    best_similarity = 0.0
    
    for expr in expressions:
        if expr.chat_id != chat_id or expr.id in exclude_ids:
            continue
        
        # 检查 style 字段
        similarity = calculate_style_similarity(target_style, expr.style)
        if similarity >= similarity_threshold and similarity > best_similarity:
            best_similarity = similarity
            best_match = expr
        
        # 检查 style_list 中的每一项
        style_list = parse_style_list(expr.style_list)
        for existing_style in style_list:
            similarity = calculate_style_similarity(target_style, existing_style)
            if similarity >= similarity_threshold and similarity > best_similarity:
                best_similarity = similarity
                best_match = expr
    
    if best_match:
        return (best_match, best_similarity)
    return None


async def compose_situation_text(content_list: List[str], summary_model: LLMRequest) -> str:
    """组合 situation 文本，尝试使用 LLM 总结"""
    sanitized = [c.strip() for c in content_list if c.strip()]
    if not sanitized:
        return ""
    
    if len(sanitized) == 1:
        return sanitized[0]
    
    # 尝试使用 LLM 总结
    prompt = (
        "请阅读以下多个聊天情境描述，并将它们概括成一句简短的话，"
        "长度不超过20个字，保留共同特点：\n"
        f"{chr(10).join(f'- {s}' for s in sanitized[-10:])}\n只输出概括内容。"
    )
    
    try:
        summary, _ = await summary_model.generate_response_async(prompt, temperature=0.2)
        summary = summary.strip()
        if summary:
            return summary
    except Exception as e:
        print(f"  ⚠️  LLM 总结 situation 失败: {e}")
    
    # 如果总结失败，返回用 "/" 连接的字符串
    return "/".join(sanitized)


async def compose_style_text(style_list: List[str], summary_model: LLMRequest) -> str:
    """组合 style 文本，尝试使用 LLM 总结"""
    sanitized = [s.strip() for s in style_list if s.strip()]
    if not sanitized:
        return ""
    
    if len(sanitized) == 1:
        return sanitized[0]
    
    # 尝试使用 LLM 总结
    prompt = (
        "请阅读以下多个语言风格/表达方式，并将它们概括成一句简短的话，"
        "长度不超过20个字，保留共同特点：\n"
        f"{chr(10).join(f'- {s}' for s in sanitized[-10:])}\n只输出概括内容。"
    )
    
    try:
        summary, _ = await summary_model.generate_response_async(prompt, temperature=0.2)
        
        print(f"Prompt:{prompt} Summary:{summary}")
        
        summary = summary.strip()
        if summary:
            return summary
    except Exception as e:
        print(f"  ⚠️  LLM 总结 style 失败: {e}")
    
    # 如果总结失败，返回第一个
    return sanitized[0]


async def simulate_merge(
    expressions: List[Expression],
    similarity_threshold: float = 0.75,
    use_llm: bool = False,
    max_samples: int = 10,
) -> Dict:
    """
    模拟合并过程
    
    Args:
        expressions: Expression 列表（从数据库读出的原始记录）
        similarity_threshold: style 相似度阈值
        use_llm: 是否使用 LLM 进行实际总结
        max_samples: 最多随机抽取的 Expression 数量（为 0 或 None 时表示不限制）
    
    Returns:
        包含合并统计信息的字典
    """
    # 如果样本太多，随机抽取一部分进行模拟，避免运行时间过长
    if max_samples and len(expressions) > max_samples:
        expressions = random.sample(expressions, max_samples)
    
    # 按 chat_id 分组
    expressions_by_chat = defaultdict(list)
    for expr in expressions:
        expressions_by_chat[expr.chat_id].append(expr)
    
    # 初始化 LLM 模型（如果需要）
    summary_model = None
    if use_llm:
        try:
            summary_model = LLMRequest(
                model_set=model_config.model_task_config.tool_use,
                request_type="expression.summary"
            )
            print("✅ LLM 模型已初始化，将进行实际总结")
        except Exception as e:
            print(f"⚠️  LLM 模型初始化失败: {e}，将跳过 LLM 总结")
            use_llm = False
    
    merge_stats = {
        "total_expressions": len(expressions),
        "total_chats": len(expressions_by_chat),
        "exact_matches": 0,
        "similar_matches": 0,
        "new_records": 0,
        "merge_details": [],
        "chat_stats": {},
        "use_llm": use_llm
    }
    
    # 为每个 chat_id 模拟合并
    for chat_id, chat_expressions in expressions_by_chat.items():
        chat_name = get_chat_name(chat_id)
        chat_stat = {
            "chat_id": chat_id,
            "chat_name": chat_name,
            "total": len(chat_expressions),
            "exact_matches": 0,
            "similar_matches": 0,
            "new_records": 0,
            "merges": []
        }
        
        processed_ids = set()
        
        for expr in chat_expressions:
            if expr.id in processed_ids:
                continue
            
            target_style = expr.style
            target_situation = expr.situation
            
            # 第一层：检查完全匹配
            exact_match = find_exact_style_match(
                chat_expressions,
                target_style,
                chat_id,
                {expr.id}
            )
            
            if exact_match:
                # 完全匹配（不使用 LLM 总结）
                # 模拟合并后的 content_list 和 style_list
                target_content_list = parse_content_list(exact_match.content_list)
                target_content_list.append(target_situation)
                
                target_style_list = parse_style_list(exact_match.style_list)
                if exact_match.style and exact_match.style not in target_style_list:
                    target_style_list.append(exact_match.style)
                if target_style not in target_style_list:
                    target_style_list.append(target_style)
                
                merge_info = {
                    "type": "exact",
                    "source_id": expr.id,
                    "target_id": exact_match.id,
                    "source_style": target_style,
                    "target_style": exact_match.style,
                    "source_situation": target_situation,
                    "target_situation": exact_match.situation,
                    "similarity": 1.0,
                    "merged_content_list": target_content_list,
                    "merged_style_list": target_style_list,
                    "merged_situation": exact_match.situation,  # 完全匹配时保持原 situation
                    "merged_style": exact_match.style  # 完全匹配时保持原 style
                }
                chat_stat["exact_matches"] += 1
                chat_stat["merges"].append(merge_info)
                merge_stats["exact_matches"] += 1
                processed_ids.add(expr.id)
                continue
            
            # 第二层：检查相似匹配
            similar_match = find_similar_style_expression(
                chat_expressions,
                target_style,
                chat_id,
                similarity_threshold,
                {expr.id}
            )
            
            if similar_match:
                match_expr, similarity = similar_match
                # 相似匹配（使用 LLM 总结）
                # 模拟合并后的 content_list 和 style_list
                target_content_list = parse_content_list(match_expr.content_list)
                target_content_list.append(target_situation)
                
                target_style_list = parse_style_list(match_expr.style_list)
                if match_expr.style and match_expr.style not in target_style_list:
                    target_style_list.append(match_expr.style)
                if target_style not in target_style_list:
                    target_style_list.append(target_style)
                
                # 使用 LLM 总结（如果启用）
                merged_situation = match_expr.situation
                merged_style = match_expr.style or target_style
                
                if use_llm and summary_model:
                    try:
                        merged_situation = await compose_situation_text(target_content_list, summary_model)
                        merged_style = await compose_style_text(target_style_list, summary_model)
                    except Exception as e:
                        print(f"  ⚠️  处理记录 {expr.id} 时 LLM 总结失败: {e}")
                        # 如果总结失败，使用 fallback
                        merged_situation = "/".join([c.strip() for c in target_content_list if c.strip()]) or match_expr.situation
                        merged_style = target_style_list[0] if target_style_list else (match_expr.style or target_style)
                else:
                    # 不使用 LLM 时，使用简单拼接
                    merged_situation = "/".join([c.strip() for c in target_content_list if c.strip()]) or match_expr.situation
                    merged_style = target_style_list[0] if target_style_list else (match_expr.style or target_style)
                
                merge_info = {
                    "type": "similar",
                    "source_id": expr.id,
                    "target_id": match_expr.id,
                    "source_style": target_style,
                    "target_style": match_expr.style,
                    "source_situation": target_situation,
                    "target_situation": match_expr.situation,
                    "similarity": similarity,
                    "merged_content_list": target_content_list,
                    "merged_style_list": target_style_list,
                    "merged_situation": merged_situation,
                    "merged_style": merged_style,
                    "llm_used": use_llm and summary_model is not None
                }
                chat_stat["similar_matches"] += 1
                chat_stat["merges"].append(merge_info)
                merge_stats["similar_matches"] += 1
                processed_ids.add(expr.id)
                continue
            
            # 没有匹配，作为新记录
            chat_stat["new_records"] += 1
            merge_stats["new_records"] += 1
            processed_ids.add(expr.id)
        
        merge_stats["chat_stats"][chat_id] = chat_stat
        merge_stats["merge_details"].extend(chat_stat["merges"])
    
    return merge_stats


def print_merge_results(stats: Dict, show_details: bool = True, max_details: int = 50):
    """打印合并结果"""
    print("\n" + "=" * 80)
    print("Expression 合并模拟结果")
    print("=" * 80)
    
    print("\n📊 总体统计:")
    print(f"  总 Expression 数: {stats['total_expressions']}")
    print(f"  总聊天数: {stats['total_chats']}")
    print(f"  完全匹配合并: {stats['exact_matches']}")
    print(f"  相似匹配合并: {stats['similar_matches']}")
    print(f"  新记录（无匹配）: {stats['new_records']}")
    if stats.get('use_llm'):
        print("  LLM 总结: 已启用")
    else:
        print("  LLM 总结: 未启用（仅模拟）")
    
    total_merges = stats['exact_matches'] + stats['similar_matches']
    if stats['total_expressions'] > 0:
        merge_ratio = (total_merges / stats['total_expressions']) * 100
        print(f"  合并比例: {merge_ratio:.1f}%")
    
    # 按聊天分组显示
    print("\n📋 按聊天分组统计:")
    for chat_id, chat_stat in stats['chat_stats'].items():
        print(f"\n  {chat_stat['chat_name']} ({chat_id[:8]}...):")
        print(f"    总数: {chat_stat['total']}")
        print(f"    完全匹配: {chat_stat['exact_matches']}")
        print(f"    相似匹配: {chat_stat['similar_matches']}")
        print(f"    新记录: {chat_stat['new_records']}")
    
    # 显示合并详情
    if show_details and stats['merge_details']:
        print(f"\n📝 合并详情 (显示前 {min(max_details, len(stats['merge_details']))} 条):")
        print()
        
        for idx, merge in enumerate(stats['merge_details'][:max_details], 1):
            merge_type = "完全匹配" if merge['type'] == 'exact' else f"相似匹配 (相似度: {merge['similarity']:.3f})"
            print(f"  {idx}. {merge_type}")
            print(f"     源记录 ID: {merge['source_id']}")
            print(f"     目标记录 ID: {merge['target_id']}")
            print(f"     源 Style: {merge['source_style'][:50]}")
            print(f"     目标 Style: {merge['target_style'][:50]}")
            print(f"     源 Situation: {merge['source_situation'][:50]}")
            print(f"     目标 Situation: {merge['target_situation'][:50]}")
            
            # 显示合并后的结果
            if 'merged_situation' in merge:
                print(f"     → 合并后 Situation: {merge['merged_situation'][:50]}")
            if 'merged_style' in merge:
                print(f"     → 合并后 Style: {merge['merged_style'][:50]}")
            if merge.get('llm_used'):
                print("     → LLM 总结: 已使用")
            elif merge['type'] == 'similar':
                print("     → LLM 总结: 未使用（模拟模式）")
            
            # 显示合并后的列表
            if 'merged_content_list' in merge and len(merge['merged_content_list']) > 1:
                print(f"     → Content List ({len(merge['merged_content_list'])} 项): {', '.join(merge['merged_content_list'][:3])}")
                if len(merge['merged_content_list']) > 3:
                    print(f"       ... 还有 {len(merge['merged_content_list']) - 3} 项")
            if 'merged_style_list' in merge and len(merge['merged_style_list']) > 1:
                print(f"     → Style List ({len(merge['merged_style_list'])} 项): {', '.join(merge['merged_style_list'][:3])}")
                if len(merge['merged_style_list']) > 3:
                    print(f"       ... 还有 {len(merge['merged_style_list']) - 3} 项")
            print()
        
        if len(stats['merge_details']) > max_details:
            print(f"  ... 还有 {len(stats['merge_details']) - max_details} 条合并记录未显示")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="模拟 Expression 合并过程")
    parser.add_argument(
        "--chat-id",
        type=str,
        default=None,
        help="指定要分析的 chat_id（不指定则分析所有）"
    )
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=0.75,
        help="相似度阈值 (0-1, 默认: 0.75)"
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="不显示详细信息，只显示统计"
    )
    parser.add_argument(
        "--max-details",
        type=int,
        default=50,
        help="最多显示的合并详情数 (默认: 50)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出文件路径 (默认: 自动生成带时间戳的文件)"
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="启用 LLM 进行实际总结（默认: 仅模拟，不调用 LLM）"
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=10,
        help="最多随机抽取的 Expression 数量 (默认: 10，设置为 0 表示不限制)"
    )
    
    args = parser.parse_args()
    
    # 验证阈值
    if not 0 <= args.similarity_threshold <= 1:
        print("错误: similarity-threshold 必须在 0-1 之间")
        return
    
    # 确定输出文件路径
    if args.output:
        output_file = args.output
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(project_root, "data", "temp")
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"expression_merge_simulation_{timestamp}.txt")
    
    # 查询 Expression 记录
    print("正在从数据库加载Expression数据...")
    try:
        if args.chat_id:
            expressions = list(Expression.select().where(Expression.chat_id == args.chat_id))
            print(f"✅ 成功加载 {len(expressions)} 条Expression记录 (chat_id: {args.chat_id})")
        else:
            expressions = list(Expression.select())
            print(f"✅ 成功加载 {len(expressions)} 条Expression记录")
    except Exception as e:
        print(f"❌ 加载数据失败: {e}")
        return
    
    if not expressions:
        print("❌ 数据库中没有找到Expression记录")
        return
    
    # 执行合并模拟
    print(f"\n正在模拟合并过程（相似度阈值: {args.similarity_threshold}，最大样本数: {args.max_samples}）...")
    if args.use_llm:
        print("⚠️  已启用 LLM 总结，将进行实际的 API 调用")
    else:
        print("ℹ️  未启用 LLM 总结，仅进行模拟（使用 --use-llm 启用实际 LLM 调用）")
    
    stats = asyncio.run(
        simulate_merge(
            expressions,
            similarity_threshold=args.similarity_threshold,
            use_llm=args.use_llm,
            max_samples=args.max_samples,
        )
    )
    
    # 输出结果
    original_stdout = sys.stdout
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            sys.stdout = f
            print_merge_results(stats, show_details=not args.no_details, max_details=args.max_details)
        sys.stdout = original_stdout
        
        # 同时在控制台输出
        print_merge_results(stats, show_details=not args.no_details, max_details=args.max_details)
        
    except Exception as e:
        sys.stdout = original_stdout
        print(f"❌ 写入文件失败: {e}")
        return
    
    print(f"\n✅ 模拟结果已保存到: {output_file}")


if __name__ == "__main__":
    main()

