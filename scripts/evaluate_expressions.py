"""
表达方式评估脚本

功能：
1. 随机读取10条表达方式，获取其situation和style
2. 使用LLM对表达方式进行评估（每个表达方式单独评估）
3. 如果合适，就通过，如果不合适，就丢弃
4. 不真正修改数据库，只是做评估
"""

import asyncio
import random
import json
import sys
import os

# 添加项目根目录到路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from src.common.database.database_model import Expression
from src.common.database.database import db
from src.llm_models.utils_model import LLMRequest
from src.config.config import model_config
from src.common.logger import get_logger

logger = get_logger("expression_evaluator")


def get_random_expressions(count: int = 10) -> list[Expression]:
    """
    随机读取指定数量的表达方式
    
    Args:
        count: 要读取的数量，默认10条
        
    Returns:
        表达方式列表
    """
    try:
        # 查询所有表达方式
        all_expressions = list(Expression.select())
        
        if not all_expressions:
            logger.warning("数据库中没有表达方式记录")
            return []
        
        # 如果总数少于请求数量，返回所有
        if len(all_expressions) <= count:
            logger.info(f"数据库中共有 {len(all_expressions)} 条表达方式，全部返回")
            return all_expressions
        
        # 随机选择指定数量
        selected = random.sample(all_expressions, count)
        logger.info(f"从 {len(all_expressions)} 条表达方式中随机选择了 {len(selected)} 条")
        return selected
        
    except Exception as e:
        logger.error(f"随机读取表达方式失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return []


def create_evaluation_prompt(situation: str, style: str) -> str:
    """
    创建评估提示词
    
    Args:
        situation: 情境
        style: 风格
        
    Returns:
        评估提示词
    """
    prompt = f"""请评估以下表达方式是否合适：

情境（situation）：{situation}
风格（style）：{style}

请从以下方面进行评估：
1. 情境描述是否清晰、准确
2. 风格表达是否合理、自然
3. 情境和风格是否匹配
4. 是否存在不当内容或表达

请以JSON格式输出评估结果：
{{
    "suitable": true/false,
    "reason": "评估理由（如果不合适，请说明原因）"
}}

如果合适，suitable设为true；如果不合适，suitable设为false，并在reason中说明原因。
请严格按照JSON格式输出，不要包含其他内容。"""
    
    return prompt


async def evaluate_expression(expression: Expression, llm: LLMRequest) -> dict:
    """
    使用LLM评估单个表达方式
    
    Args:
        expression: 表达方式对象
        llm: LLM请求实例
        
    Returns:
        评估结果字典，包含：
        - expression_id: 表达方式ID
        - situation: 情境
        - style: 风格
        - suitable: 是否合适
        - reason: 评估理由
        - error: 错误信息（如果有）
    """
    result = {
        "expression_id": expression.id,
        "situation": expression.situation,
        "style": expression.style,
        "suitable": None,
        "reason": None,
        "error": None
    }
    
    try:
        # 创建评估提示词
        prompt = create_evaluation_prompt(expression.situation, expression.style)
        
        # 调用LLM进行评估
        logger.info(f"正在评估表达方式 ID: {expression.id}, Situation: {expression.situation}, Style: {expression.style}")
        response, (reasoning, model_name, _) = await llm.generate_response_async(
            prompt=prompt,
            temperature=0.3,
            max_tokens=500
        )
        
        logger.debug(f"LLM响应: {response}")
        logger.debug(f"使用模型: {model_name}")
        
        # 解析JSON响应
        try:
            # 尝试直接解析
            evaluation = json.loads(response)
        except json.JSONDecodeError:
            # 如果直接解析失败，尝试提取JSON部分
            import re
            json_match = re.search(r'\{[^{}]*"suitable"[^{}]*\}', response, re.DOTALL)
            if json_match:
                evaluation = json.loads(json_match.group())
            else:
                raise ValueError("无法从响应中提取JSON格式的评估结果")
        
        # 提取评估结果
        result["suitable"] = evaluation.get("suitable", False)
        result["reason"] = evaluation.get("reason", "未提供理由")
        
        logger.info(f"表达方式 ID: {expression.id} 评估结果: {'通过' if result['suitable'] else '不通过'}")
        if result["reason"]:
            logger.info(f"评估理由: {result['reason']}")
            
    except Exception as e:
        logger.error(f"评估表达方式 ID: {expression.id} 时出错: {e}")
        import traceback
        logger.error(traceback.format_exc())
        result["error"] = str(e)
        result["suitable"] = False
        result["reason"] = f"评估过程出错: {str(e)}"
    
    return result


async def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("开始表达方式评估")
    logger.info("=" * 60)
    
    # 初始化数据库连接
    try:
        db.connect(reuse_if_open=True)
        logger.info("数据库连接成功")
    except Exception as e:
        logger.error(f"数据库连接失败: {e}")
        return
    
    # 1. 随机读取10条表达方式
    logger.info("\n步骤1: 随机读取10条表达方式")
    expressions = get_random_expressions(10)
    
    if not expressions:
        logger.error("没有可用的表达方式，退出")
        return
    
    logger.info(f"成功读取 {len(expressions)} 条表达方式")
    for i, expr in enumerate(expressions, 1):
        logger.info(f"  {i}. ID: {expr.id}, Situation: {expr.situation}, Style: {expr.style}")
    
    # 2. 创建LLM实例
    logger.info("\n步骤2: 创建LLM实例")
    try:
        llm = LLMRequest(
            model_set=model_config.model_task_config.tool_use,
            request_type="expression_evaluator"
        )
        logger.info("LLM实例创建成功")
    except Exception as e:
        logger.error(f"创建LLM实例失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return
    
    # 3. 对每个表达方式进行评估
    logger.info("\n步骤3: 开始评估表达方式")
    results = []
    
    for i, expression in enumerate(expressions, 1):
        logger.info(f"\n--- 评估进度: {i}/{len(expressions)} ---")
        result = await evaluate_expression(expression, llm)
        results.append(result)
        
        # 添加短暂延迟，避免请求过快
        if i < len(expressions):
            await asyncio.sleep(0.5)
    
    # 4. 汇总结果
    logger.info("\n" + "=" * 60)
    logger.info("评估结果汇总")
    logger.info("=" * 60)
    
    passed = [r for r in results if r["suitable"] is True]
    failed = [r for r in results if r["suitable"] is False]
    errors = [r for r in results if r["error"] is not None]
    
    logger.info(f"\n总计: {len(results)} 条")
    logger.info(f"通过: {len(passed)} 条")
    logger.info(f"不通过: {len(failed)} 条")
    if errors:
        logger.info(f"出错: {len(errors)} 条")
    
    # 详细结果
    logger.info("\n--- 通过的表达方式 ---")
    if passed:
        for r in passed:
            logger.info(f"  ID: {r['expression_id']}")
            logger.info(f"    Situation: {r['situation']}")
            logger.info(f"    Style: {r['style']}")
            if r['reason']:
                logger.info(f"    理由: {r['reason']}")
    else:
        logger.info("  无")
    
    logger.info("\n--- 不通过的表达方式 ---")
    if failed:
        for r in failed:
            logger.info(f"  ID: {r['expression_id']}")
            logger.info(f"    Situation: {r['situation']}")
            logger.info(f"    Style: {r['style']}")
            if r['reason']:
                logger.info(f"    理由: {r['reason']}")
            if r['error']:
                logger.info(f"    错误: {r['error']}")
    else:
        logger.info("  无")
    
    # 保存结果到JSON文件（可选）
    output_file = os.path.join(project_root, "data", "expression_evaluation_results.json")
    try:
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump({
                "total": len(results),
                "passed": len(passed),
                "failed": len(failed),
                "errors": len(errors),
                "results": results
            }, f, ensure_ascii=False, indent=2)
        logger.info(f"\n评估结果已保存到: {output_file}")
    except Exception as e:
        logger.warning(f"保存结果到文件失败: {e}")
    
    logger.info("\n" + "=" * 60)
    logger.info("评估完成")
    logger.info("=" * 60)
    
    # 关闭数据库连接
    try:
        db.close()
        logger.info("数据库连接已关闭")
    except Exception as e:
        logger.warning(f"关闭数据库连接时出错: {e}")


if __name__ == "__main__":
    asyncio.run(main())

