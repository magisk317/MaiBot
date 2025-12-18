"""
分析expression库中situation和style的相似度

用法:
    python scripts/expression_similarity_analysis.py
    或指定阈值:
    python scripts/expression_similarity_analysis.py --situation-threshold 0.8 --style-threshold 0.7
"""

import sys
import os
import argparse
from typing import List, Tuple
from collections import defaultdict
from difflib import SequenceMatcher
from datetime import datetime

# Add project root to Python path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Import after setting up path (required for project imports)
from src.common.database.database_model import Expression, ChatStreams  # noqa: E402
from src.config.config import global_config  # noqa: E402
import hashlib  # noqa: E402


class TeeOutput:
    """同时输出到控制台和文件的类"""
    def __init__(self, file_path: str):
        self.file = open(file_path, "w", encoding="utf-8")
        self.console = sys.stdout
    
    def write(self, text: str):
        """写入文本到控制台和文件"""
        self.console.write(text)
        self.file.write(text)
        self.file.flush()  # 立即刷新到文件
    
    def flush(self):
        """刷新输出"""
        self.console.flush()
        self.file.flush()
    
    def close(self):
        """关闭文件"""
        if self.file:
            self.file.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


def _parse_stream_config_to_chat_id(stream_config_str: str) -> str | None:
    """
    解析'platform:id:type'为chat_id（与ExpressionSelector中的逻辑一致）
    """
    try:
        parts = stream_config_str.split(":")
        if len(parts) != 3:
            return None
        platform = parts[0]
        id_str = parts[1]
        stream_type = parts[2]
        is_group = stream_type == "group"
        if is_group:
            components = [platform, str(id_str)]
        else:
            components = [platform, str(id_str), "private"]
        key = "_".join(components)
        return hashlib.md5(key.encode()).hexdigest()
    except Exception:
        return None


def build_chat_id_groups() -> dict[str, set[str]]:
    """
    根据expression_groups配置，构建chat_id到相关chat_id集合的映射
    
    Returns:
        dict: {chat_id: set of related chat_ids (including itself)}
    """
    groups = global_config.expression.expression_groups
    chat_id_groups: dict[str, set[str]] = {}
    
    # 检查是否存在全局共享组（包含"*"的组）
    global_group_exists = any("*" in group for group in groups)
    
    if global_group_exists:
        # 如果存在全局共享组，收集所有配置中的chat_id
        all_chat_ids = set()
        for group in groups:
            for stream_config_str in group:
                if stream_config_str == "*":
                    continue
                if chat_id_candidate := _parse_stream_config_to_chat_id(stream_config_str):
                    all_chat_ids.add(chat_id_candidate)
        
        # 所有chat_id都互相相关
        for chat_id in all_chat_ids:
            chat_id_groups[chat_id] = all_chat_ids.copy()
    else:
        # 处理普通组
        for group in groups:
            group_chat_ids = set()
            for stream_config_str in group:
                if chat_id_candidate := _parse_stream_config_to_chat_id(stream_config_str):
                    group_chat_ids.add(chat_id_candidate)
            
            # 组内的所有chat_id都互相相关
            for chat_id in group_chat_ids:
                if chat_id not in chat_id_groups:
                    chat_id_groups[chat_id] = set()
                chat_id_groups[chat_id].update(group_chat_ids)
    
    # 确保每个chat_id至少包含自身
    for chat_id in chat_id_groups:
        chat_id_groups[chat_id].add(chat_id)
    
    return chat_id_groups


def are_chat_ids_related(chat_id1: str, chat_id2: str, chat_id_groups: dict[str, set[str]]) -> bool:
    """
    判断两个chat_id是否相关（相同或同组）
    
    Args:
        chat_id1: 第一个chat_id
        chat_id2: 第二个chat_id
        chat_id_groups: chat_id到相关chat_id集合的映射
    
    Returns:
        bool: 如果两个chat_id相同或同组，返回True
    """
    if chat_id1 == chat_id2:
        return True
    
    # 如果chat_id1在映射中，检查chat_id2是否在其相关集合中
    if chat_id1 in chat_id_groups:
        return chat_id2 in chat_id_groups[chat_id1]
    
    # 如果chat_id1不在映射中，说明它不在任何组中，只与自己相关
    return False


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


def text_similarity(text1: str, text2: str) -> float:
    """
    计算两个文本的相似度
    使用SequenceMatcher计算相似度，返回0-1之间的值
    在计算前会移除"使用"和"句式"这两个词
    """
    if not text1 or not text2:
        return 0.0
    
    # 移除"使用"和"句式"这两个词
    def remove_ignored_words(text: str) -> str:
        """移除需要忽略的词"""
        text = text.replace("使用", "")
        text = text.replace("句式", "")
        return text.strip()
    
    cleaned_text1 = remove_ignored_words(text1)
    cleaned_text2 = remove_ignored_words(text2)
    
    # 如果清理后文本为空，返回0
    if not cleaned_text1 or not cleaned_text2:
        return 0.0
    
    return SequenceMatcher(None, cleaned_text1, cleaned_text2).ratio()


def find_similar_pairs(
    expressions: List[Expression],
    field_name: str,
    threshold: float,
    max_pairs: int = None
) -> List[Tuple[int, int, float, str, str]]:
    """
    找出相似的expression对
    
    Args:
        expressions: Expression对象列表
        field_name: 要比较的字段名 ('situation' 或 'style')
        threshold: 相似度阈值 (0-1)
        max_pairs: 最多返回的对数，None表示返回所有
    
    Returns:
        List of (index1, index2, similarity, text1, text2) tuples
    """
    similar_pairs = []
    n = len(expressions)
    
    print(f"正在分析 {field_name} 字段的相似度...")
    print(f"总共需要比较 {n * (n - 1) // 2} 对...")
    
    for i in range(n):
        if (i + 1) % 100 == 0:
            print(f"  已处理 {i + 1}/{n} 个项目...")
        
        expr1 = expressions[i]
        text1 = getattr(expr1, field_name, "")
        
        for j in range(i + 1, n):
            expr2 = expressions[j]
            text2 = getattr(expr2, field_name, "")
            
            similarity = text_similarity(text1, text2)
            
            if similarity >= threshold:
                similar_pairs.append((i, j, similarity, text1, text2))
    
    # 按相似度降序排序
    similar_pairs.sort(key=lambda x: x[2], reverse=True)
    
    if max_pairs:
        similar_pairs = similar_pairs[:max_pairs]
    
    return similar_pairs


def group_similar_items(
    expressions: List[Expression],
    field_name: str,
    threshold: float,
    chat_id_groups: dict[str, set[str]]
) -> List[List[int]]:
    """
    将相似的expression分组（仅比较相同chat_id或同组的项目）
    
    Args:
        expressions: Expression对象列表
        field_name: 要比较的字段名 ('situation' 或 'style')
        threshold: 相似度阈值 (0-1)
        chat_id_groups: chat_id到相关chat_id集合的映射
    
    Returns:
        List of groups, each group is a list of indices
    """
    n = len(expressions)
    # 使用并查集的思想来分组
    parent = list(range(n))
    
    def find(x):
        if parent[x] != x:
            parent[x] = find(parent[x])
        return parent[x]
    
    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py
    
    print(f"正在对 {field_name} 字段进行分组（仅比较相同chat_id或同组的项目）...")
    
    # 统计需要比较的对数
    total_pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            if are_chat_ids_related(expressions[i].chat_id, expressions[j].chat_id, chat_id_groups):
                total_pairs += 1
    
    print(f"总共需要比较 {total_pairs} 对（已过滤不同chat_id且不同组的项目）...")
    
    compared_pairs = 0
    for i in range(n):
        if (i + 1) % 100 == 0:
            print(f"  已处理 {i + 1}/{n} 个项目...")
        
        expr1 = expressions[i]
        text1 = getattr(expr1, field_name, "")
        
        for j in range(i + 1, n):
            expr2 = expressions[j]
            
            # 只比较相同chat_id或同组的项目
            if not are_chat_ids_related(expr1.chat_id, expr2.chat_id, chat_id_groups):
                continue
            
            compared_pairs += 1
            text2 = getattr(expr2, field_name, "")
            
            similarity = text_similarity(text1, text2)
            
            if similarity >= threshold:
                union(i, j)
    
    # 收集分组
    groups = defaultdict(list)
    for i in range(n):
        root = find(i)
        groups[root].append(i)
    
    # 只返回包含多个项目的组
    result = [group for group in groups.values() if len(group) > 1]
    result.sort(key=len, reverse=True)
    
    return result


def print_similarity_analysis(
    expressions: List[Expression],
    field_name: str,
    threshold: float,
    chat_id_groups: dict[str, set[str]],
    show_details: bool = True,
    max_groups: int = 20
):
    """打印相似度分析结果"""
    print("\n" + "=" * 80)
    print(f"{field_name.upper()} 相似度分析 (阈值: {threshold})")
    print("=" * 80)
    
    # 分组分析
    groups = group_similar_items(expressions, field_name, threshold, chat_id_groups)
    
    total_items = len(expressions)
    similar_items_count = sum(len(group) for group in groups)
    unique_groups = len(groups)
    
    print("\n📊 统计信息:")
    print(f"  总项目数: {total_items}")
    print(f"  相似项目数: {similar_items_count} ({similar_items_count / total_items * 100:.1f}%)")
    print(f"  相似组数: {unique_groups}")
    print(f"  平均每组项目数: {similar_items_count / unique_groups:.1f}" if unique_groups > 0 else "  平均每组项目数: 0")
    
    if not groups:
        print(f"\n未找到相似度 >= {threshold} 的项目组")
        return
    
    print(f"\n📋 相似组详情 (显示前 {min(max_groups, len(groups))} 组):")
    print()
    
    for group_idx, group in enumerate(groups[:max_groups], 1):
        print(f"组 {group_idx} (共 {len(group)} 个项目):")
        
        if show_details:
            # 显示组内所有项目的详细信息
            for idx in group:
                expr = expressions[idx]
                text = getattr(expr, field_name, "")
                chat_name = get_chat_name(expr.chat_id)
                
                # 截断过长的文本
                display_text = text[:60] + "..." if len(text) > 60 else text
                
                print(f"  [{expr.id}] {display_text}")
                print(f"     聊天: {chat_name}, Count: {expr.count}")
            
            # 计算组内平均相似度
            if len(group) > 1:
                similarities = []
                above_threshold_pairs = []  # 存储满足阈值的相似对
                above_threshold_count = 0
                for i in range(len(group)):
                    for j in range(i + 1, len(group)):
                        text1 = getattr(expressions[group[i]], field_name, "")
                        text2 = getattr(expressions[group[j]], field_name, "")
                        sim = text_similarity(text1, text2)
                        similarities.append(sim)
                        if sim >= threshold:
                            above_threshold_count += 1
                            # 存储满足阈值的对的信息
                            expr1 = expressions[group[i]]
                            expr2 = expressions[group[j]]
                            display_text1 = text1[:40] + "..." if len(text1) > 40 else text1
                            display_text2 = text2[:40] + "..." if len(text2) > 40 else text2
                            above_threshold_pairs.append((
                                expr1.id, display_text1,
                                expr2.id, display_text2,
                                sim
                            ))
                
                if similarities:
                    avg_sim = sum(similarities) / len(similarities)
                    min_sim = min(similarities)
                    max_sim = max(similarities)
                    above_threshold_ratio = above_threshold_count / len(similarities) * 100
                    print(f"     平均相似度: {avg_sim:.3f} (范围: {min_sim:.3f} - {max_sim:.3f})")
                    print(f"     满足阈值({threshold})的比例: {above_threshold_ratio:.1f}% ({above_threshold_count}/{len(similarities)})")
                    
                    # 显示满足阈值的相似对（这些是直接连接，导致它们被分到一组）
                    if above_threshold_pairs:
                        print("     ⚠️  直接相似的对 (这些对导致它们被分到一组):")
                        # 按相似度降序排序
                        above_threshold_pairs.sort(key=lambda x: x[4], reverse=True)
                        for idx1, text1, idx2, text2, sim in above_threshold_pairs[:10]:  # 最多显示10对
                            print(f"       [{idx1}] ↔ [{idx2}]: {sim:.3f}")
                            print(f"          \"{text1}\" ↔ \"{text2}\"")
                        if len(above_threshold_pairs) > 10:
                            print(f"       ... 还有 {len(above_threshold_pairs) - 10} 对满足阈值")
                    else:
                        print(f"     ⚠️  警告: 组内没有任何对满足阈值({threshold:.2f})，可能是通过传递性连接")
        else:
            # 只显示组内第一个项目作为示例
            expr = expressions[group[0]]
            text = getattr(expr, field_name, "")
            display_text = text[:60] + "..." if len(text) > 60 else text
            print(f"  示例: {display_text}")
            print(f"  ... 还有 {len(group) - 1} 个相似项目")
        
        print()
    
    if len(groups) > max_groups:
        print(f"... 还有 {len(groups) - max_groups} 组未显示")


def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="分析expression库中situation和style的相似度")
    parser.add_argument(
        "--situation-threshold",
        type=float,
        default=0.7,
        help="situation相似度阈值 (0-1, 默认: 0.7)"
    )
    parser.add_argument(
        "--style-threshold",
        type=float,
        default=0.7,
        help="style相似度阈值 (0-1, 默认: 0.7)"
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="不显示详细信息，只显示统计"
    )
    parser.add_argument(
        "--max-groups",
        type=int,
        default=20,
        help="最多显示的组数 (默认: 20)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="输出文件路径 (默认: 自动生成带时间戳的文件)"
    )
    
    args = parser.parse_args()
    
    # 验证阈值
    if not 0 <= args.situation_threshold <= 1:
        print("错误: situation-threshold 必须在 0-1 之间")
        return
    if not 0 <= args.style_threshold <= 1:
        print("错误: style-threshold 必须在 0-1 之间")
        return
    
    # 确定输出文件路径
    if args.output:
        output_file = args.output
    else:
        # 自动生成带时间戳的输出文件
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = os.path.join(project_root, "data", "temp")
        os.makedirs(output_dir, exist_ok=True)
        output_file = os.path.join(output_dir, f"expression_similarity_analysis_{timestamp}.txt")
    
    # 使用TeeOutput同时输出到控制台和文件
    with TeeOutput(output_file) as tee:
        # 临时替换sys.stdout
        original_stdout = sys.stdout
        sys.stdout = tee
        
        try:
            print("=" * 80)
            print("Expression 相似度分析工具")
            print("=" * 80)
            print(f"输出文件: {output_file}")
            print()
            
            _run_analysis(args)
            
        finally:
            # 恢复原始stdout
            sys.stdout = original_stdout
    
    print(f"\n✅ 分析结果已保存到: {output_file}")


def _run_analysis(args):
    """执行分析的主逻辑"""
    
    # 查询所有Expression记录
    print("正在从数据库加载Expression数据...")
    try:
        expressions = list(Expression.select())
    except Exception as e:
        print(f"❌ 加载数据失败: {e}")
        return
    
    if not expressions:
        print("❌ 数据库中没有找到Expression记录")
        return
    
    print(f"✅ 成功加载 {len(expressions)} 条Expression记录")
    print()
    
    # 构建chat_id分组映射
    print("正在构建chat_id分组映射（根据expression_groups配置）...")
    try:
        chat_id_groups = build_chat_id_groups()
        print(f"✅ 成功构建 {len(chat_id_groups)} 个chat_id的分组映射")
        if chat_id_groups:
            # 统计分组信息
            total_related = sum(len(related) for related in chat_id_groups.values())
            avg_related = total_related / len(chat_id_groups)
            print(f"   平均每个chat_id与 {avg_related:.1f} 个chat_id相关（包括自身）")
        print()
    except Exception as e:
        print(f"⚠️  构建chat_id分组映射失败: {e}")
        print("   将使用默认行为：只比较相同chat_id的项目")
        chat_id_groups = {}
    
    # 分析situation相似度
    print_similarity_analysis(
        expressions,
        "situation",
        args.situation_threshold,
        chat_id_groups,
        show_details=not args.no_details,
        max_groups=args.max_groups
    )
    
    # 分析style相似度
    print_similarity_analysis(
        expressions,
        "style",
        args.style_threshold,
        chat_id_groups,
        show_details=not args.no_details,
        max_groups=args.max_groups
    )
    
    print("\n" + "=" * 80)
    print("分析完成！")
    print("=" * 80)


if __name__ == "__main__":
    main()

