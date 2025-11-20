"""统计数据 API 路由"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, Any, List
from datetime import datetime, timedelta
from collections import defaultdict

from src.common.logger import get_logger
from src.common.database.database_model import LLMUsage, OnlineTime, Messages

logger = get_logger("webui.statistics")

router = APIRouter(prefix="/statistics", tags=["statistics"])


class StatisticsSummary(BaseModel):
    """统计数据摘要"""
    total_requests: int = Field(0, description="总请求数")
    total_cost: float = Field(0.0, description="总花费")
    total_tokens: int = Field(0, description="总token数")
    online_time: float = Field(0.0, description="在线时间（秒）")
    total_messages: int = Field(0, description="总消息数")
    total_replies: int = Field(0, description="总回复数")
    avg_response_time: float = Field(0.0, description="平均响应时间")
    cost_per_hour: float = Field(0.0, description="每小时花费")
    tokens_per_hour: float = Field(0.0, description="每小时token数")


class ModelStatistics(BaseModel):
    """模型统计"""
    model_name: str
    request_count: int
    total_cost: float
    total_tokens: int
    avg_response_time: float


class TimeSeriesData(BaseModel):
    """时间序列数据"""
    timestamp: str
    requests: int = 0
    cost: float = 0.0
    tokens: int = 0


class DashboardData(BaseModel):
    """仪表盘数据"""
    summary: StatisticsSummary
    model_stats: List[ModelStatistics]
    hourly_data: List[TimeSeriesData]
    daily_data: List[TimeSeriesData]
    recent_activity: List[Dict[str, Any]]


@router.get("/dashboard", response_model=DashboardData)
async def get_dashboard_data(hours: int = 24):
    """
    获取仪表盘统计数据
    
    Args:
        hours: 统计时间范围（小时），默认24小时
        
    Returns:
        仪表盘数据
    """
    try:
        now = datetime.now()
        start_time = now - timedelta(hours=hours)
        
        # 获取摘要数据
        summary = await _get_summary_statistics(start_time, now)
        
        # 获取模型统计
        model_stats = await _get_model_statistics(start_time)
        
        # 获取小时级时间序列数据
        hourly_data = await _get_hourly_statistics(start_time, now)
        
        # 获取日级时间序列数据（最近7天）
        daily_start = now - timedelta(days=7)
        daily_data = await _get_daily_statistics(daily_start, now)
        
        # 获取最近活动
        recent_activity = await _get_recent_activity(limit=10)
        
        return DashboardData(
            summary=summary,
            model_stats=model_stats,
            hourly_data=hourly_data,
            daily_data=daily_data,
            recent_activity=recent_activity
        )
    except Exception as e:
        logger.error(f"获取仪表盘数据失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取统计数据失败: {str(e)}") from e


async def _get_summary_statistics(start_time: datetime, end_time: datetime) -> StatisticsSummary:
    """获取摘要统计数据"""
    summary = StatisticsSummary()
    
    # 查询 LLM 使用记录
    llm_records = list(
        LLMUsage.select()
        .where(LLMUsage.timestamp >= start_time)
        .where(LLMUsage.timestamp <= end_time)
    )
    
    total_time_cost = 0.0
    time_cost_count = 0
    
    for record in llm_records:
        summary.total_requests += 1
        summary.total_cost += record.cost or 0.0
        summary.total_tokens += (record.prompt_tokens or 0) + (record.completion_tokens or 0)
        
        if record.time_cost and record.time_cost > 0:
            total_time_cost += record.time_cost
            time_cost_count += 1
    
    # 计算平均响应时间
    if time_cost_count > 0:
        summary.avg_response_time = total_time_cost / time_cost_count
    
    # 查询在线时间
    online_records = list(
        OnlineTime.select()
        .where(
            (OnlineTime.start_timestamp >= start_time) |
            (OnlineTime.end_timestamp >= start_time)
        )
    )
    
    for record in online_records:
        start = max(record.start_timestamp, start_time)
        end = min(record.end_timestamp, end_time)
        if end > start:
            summary.online_time += (end - start).total_seconds()
    
    # 查询消息数量
    messages = list(
        Messages.select()
        .where(Messages.time >= start_time.timestamp())
        .where(Messages.time <= end_time.timestamp())
    )
    
    summary.total_messages = len(messages)
    # 简单统计：如果 reply_to 不为空，则认为是回复
    summary.total_replies = len([m for m in messages if m.reply_to])
    
    # 计算派生指标
    if summary.online_time > 0:
        online_hours = summary.online_time / 3600.0
        summary.cost_per_hour = summary.total_cost / online_hours
        summary.tokens_per_hour = summary.total_tokens / online_hours
    
    return summary


async def _get_model_statistics(start_time: datetime) -> List[ModelStatistics]:
    """获取模型统计数据"""
    model_data = defaultdict(lambda: {
        'request_count': 0,
        'total_cost': 0.0,
        'total_tokens': 0,
        'time_costs': []
    })
    
    records = list(
        LLMUsage.select()
        .where(LLMUsage.timestamp >= start_time)
    )
    
    for record in records:
        model_name = record.model_assign_name or record.model_name or "unknown"
        model_data[model_name]['request_count'] += 1
        model_data[model_name]['total_cost'] += record.cost or 0.0
        model_data[model_name]['total_tokens'] += (record.prompt_tokens or 0) + (record.completion_tokens or 0)
        
        if record.time_cost and record.time_cost > 0:
            model_data[model_name]['time_costs'].append(record.time_cost)
    
    # 转换为列表并排序
    result = []
    for model_name, data in model_data.items():
        avg_time = sum(data['time_costs']) / len(data['time_costs']) if data['time_costs'] else 0.0
        result.append(ModelStatistics(
            model_name=model_name,
            request_count=data['request_count'],
            total_cost=data['total_cost'],
            total_tokens=data['total_tokens'],
            avg_response_time=avg_time
        ))
    
    # 按请求数排序
    result.sort(key=lambda x: x.request_count, reverse=True)
    return result[:10]  # 返回前10个


async def _get_hourly_statistics(start_time: datetime, end_time: datetime) -> List[TimeSeriesData]:
    """获取小时级统计数据"""
    # 创建小时桶
    hourly_buckets = defaultdict(lambda: {'requests': 0, 'cost': 0.0, 'tokens': 0})
    
    records = list(
        LLMUsage.select()
        .where(LLMUsage.timestamp >= start_time)
        .where(LLMUsage.timestamp <= end_time)
    )
    
    for record in records:
        # 获取小时键（去掉分钟和秒）
        hour_key = record.timestamp.replace(minute=0, second=0, microsecond=0)
        hour_str = hour_key.isoformat()
        
        hourly_buckets[hour_str]['requests'] += 1
        hourly_buckets[hour_str]['cost'] += record.cost or 0.0
        hourly_buckets[hour_str]['tokens'] += (record.prompt_tokens or 0) + (record.completion_tokens or 0)
    
    # 填充所有小时（包括没有数据的）
    result = []
    current = start_time.replace(minute=0, second=0, microsecond=0)
    while current <= end_time:
        hour_str = current.isoformat()
        data = hourly_buckets.get(hour_str, {'requests': 0, 'cost': 0.0, 'tokens': 0})
        result.append(TimeSeriesData(
            timestamp=hour_str,
            requests=data['requests'],
            cost=data['cost'],
            tokens=data['tokens']
        ))
        current += timedelta(hours=1)
    
    return result


async def _get_daily_statistics(start_time: datetime, end_time: datetime) -> List[TimeSeriesData]:
    """获取日级统计数据"""
    daily_buckets = defaultdict(lambda: {'requests': 0, 'cost': 0.0, 'tokens': 0})
    
    records = list(
        LLMUsage.select()
        .where(LLMUsage.timestamp >= start_time)
        .where(LLMUsage.timestamp <= end_time)
    )
    
    for record in records:
        # 获取日期键
        day_key = record.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        day_str = day_key.isoformat()
        
        daily_buckets[day_str]['requests'] += 1
        daily_buckets[day_str]['cost'] += record.cost or 0.0
        daily_buckets[day_str]['tokens'] += (record.prompt_tokens or 0) + (record.completion_tokens or 0)
    
    # 填充所有天
    result = []
    current = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= end_time:
        day_str = current.isoformat()
        data = daily_buckets.get(day_str, {'requests': 0, 'cost': 0.0, 'tokens': 0})
        result.append(TimeSeriesData(
            timestamp=day_str,
            requests=data['requests'],
            cost=data['cost'],
            tokens=data['tokens']
        ))
        current += timedelta(days=1)
    
    return result


async def _get_recent_activity(limit: int = 10) -> List[Dict[str, Any]]:
    """获取最近活动"""
    records = list(
        LLMUsage.select()
        .order_by(LLMUsage.timestamp.desc())
        .limit(limit)
    )
    
    activities = []
    for record in records:
        activities.append({
            'timestamp': record.timestamp.isoformat(),
            'model': record.model_assign_name or record.model_name,
            'request_type': record.request_type,
            'tokens': (record.prompt_tokens or 0) + (record.completion_tokens or 0),
            'cost': record.cost or 0.0,
            'time_cost': record.time_cost or 0.0,
            'status': record.status
        })
    
    return activities


@router.get("/summary")
async def get_summary(hours: int = 24):
    """
    获取统计摘要
    
    Args:
        hours: 统计时间范围（小时）
    """
    try:
        now = datetime.now()
        start_time = now - timedelta(hours=hours)
        summary = await _get_summary_statistics(start_time, now)
        return summary
    except Exception as e:
        logger.error(f"获取统计摘要失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/models")
async def get_model_stats(hours: int = 24):
    """
    获取模型统计
    
    Args:
        hours: 统计时间范围（小时）
    """
    try:
        now = datetime.now()
        start_time = now - timedelta(hours=hours)
        stats = await _get_model_statistics(start_time)
        return stats
    except Exception as e:
        logger.error(f"获取模型统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
