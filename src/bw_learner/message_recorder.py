import time
import asyncio
from typing import List, Any, Optional
from collections import OrderedDict
from dataclasses import dataclass
from src.common.logger import get_logger
from src.config.config import global_config
from src.chat.message_receive.chat_stream import get_chat_manager
from src.chat.utils.chat_message_builder import get_raw_msg_by_timestamp_with_chat_inclusive
from src.bw_learner.expression_learner import expression_learner_manager
from src.bw_learner.jargon_miner import miner_manager
from src.person_info.person_info import Person

logger = get_logger("bw_learner")


@dataclass
class PersonInfo:
    """参与聊天的人物信息"""
    user_id: str
    user_platform: str
    user_nickname: str
    user_cardname: Optional[str]
    person_name: str
    last_seen_time: float  # 最后发言时间
    
    def get_unique_key(self) -> str:
        """获取唯一标识（用于去重）"""
        return f"{self.user_platform}:{self.user_id}"


class MessageRecorder:
    """
    统一的消息记录器，负责管理时间窗口和消息提取，并将消息分发给 expression_learner 和 jargon_miner
    """
    
    def __init__(self, chat_id: str) -> None:
        self.chat_id = chat_id
        self.chat_stream = get_chat_manager().get_stream(chat_id)
        self.chat_name = get_chat_manager().get_stream_name(chat_id) or chat_id
        
        # 维护每个chat的上次提取时间
        self.last_extraction_time: float = time.time()
        
        # 提取锁，防止并发执行
        self._extraction_lock = asyncio.Lock()
        
        # 维护参与该chat_id的人物列表（最多30个，使用OrderedDict保持插入顺序）
        # key: f"{platform}:{user_id}", value: PersonInfo
        self._person_list: OrderedDict[str, PersonInfo] = OrderedDict()
        self._max_person_count = 30
        
        # 获取 expression 和 jargon 的配置参数
        self._init_parameters()
        
        # 获取 expression_learner 和 jargon_miner 实例
        self.expression_learner = expression_learner_manager.get_expression_learner(chat_id)
        self.jargon_miner = miner_manager.get_miner(chat_id)
    
    def _init_parameters(self) -> None:
        """初始化提取参数"""
        # 获取 expression 配置
        _, self.enable_expression_learning, self.enable_jargon_learning = (
            global_config.expression.get_expression_config_for_chat(self.chat_id)
        )
        self.min_messages_for_extraction = 30
        self.min_extraction_interval = 60
        
        logger.debug(
            f"MessageRecorder 初始化: chat_id={self.chat_id}, "
            f"min_messages={self.min_messages_for_extraction}, "
            f"min_interval={self.min_extraction_interval}"
        )
    
    def should_trigger_extraction(self) -> bool:
        """
        检查是否应该触发消息提取
        
        Returns:
            bool: 是否应该触发提取
        """
        # 检查时间间隔
        time_diff = time.time() - self.last_extraction_time
        if time_diff < self.min_extraction_interval:
            return False
        
        # 检查消息数量
        recent_messages = get_raw_msg_by_timestamp_with_chat_inclusive(
            chat_id=self.chat_id,
            timestamp_start=self.last_extraction_time,
            timestamp_end=time.time(),
        )
        
        if not recent_messages or len(recent_messages) < self.min_messages_for_extraction:
            return False
        
        return True
    
    async def extract_and_distribute(self) -> None:
        """
        提取消息并分发给 expression_learner 和 jargon_miner
        """
        # 使用异步锁防止并发执行
        async with self._extraction_lock:
            # 在锁内检查，避免并发触发
            if not self.should_trigger_extraction():
                return
            
            # 检查 chat_stream 是否存在
            if not self.chat_stream:
                return
            
            # 记录本次提取的时间窗口，避免重复提取
            extraction_start_time = self.last_extraction_time
            extraction_end_time = time.time()
            
            # 立即更新提取时间，防止并发触发
            self.last_extraction_time = extraction_end_time
            
            try:
                logger.info(f"在聊天流 {self.chat_name} 开始统一消息提取和分发")
                
                # 拉取提取窗口内的消息
                messages = get_raw_msg_by_timestamp_with_chat_inclusive(
                    chat_id=self.chat_id,
                    timestamp_start=extraction_start_time,
                    timestamp_end=extraction_end_time,
                )
                
                if not messages:
                    logger.debug(f"聊天流 {self.chat_name} 没有新消息，跳过提取")
                    return
                
                # 按时间排序，确保顺序一致
                messages = sorted(messages, key=lambda msg: msg.time or 0)
                
                # 更新参与聊天的人物列表
                self._update_person_list(messages)
                
                logger.info(f"聊天流 {self.chat_name} 的人物列表: {self._person_list}")
                
                logger.info(
                    f"聊天流 {self.chat_name} 提取到 {len(messages)} 条消息，"
                    f"时间窗口: {extraction_start_time:.2f} - {extraction_end_time:.2f}"
                )
                
                
                # 分别触发 expression_learner 和 jargon_miner 的处理
                # 传递提取的消息，避免它们重复获取
                # 触发 expression 学习（如果启用）
                if self.enable_expression_learning:
                    asyncio.create_task(
                        self._trigger_expression_learning(extraction_start_time, extraction_end_time, messages)
                    )
                
                # 触发 jargon 提取（如果启用），传递消息
                # if self.enable_jargon_learning:
                    # asyncio.create_task(
                        # self._trigger_jargon_extraction(extraction_start_time, extraction_end_time, messages)
                    # )
                
            except Exception as e:
                logger.error(f"为聊天流 {self.chat_name} 提取和分发消息失败: {e}")
                import traceback
                traceback.print_exc()
                # 即使失败也保持时间戳更新，避免频繁重试
    
    async def _trigger_expression_learning(
        self, 
        timestamp_start: float, 
        timestamp_end: float,
        messages: List[Any]
    ) -> None:
        """
        触发 expression 学习，使用指定的消息列表
        
        Args:
            timestamp_start: 开始时间戳
            timestamp_end: 结束时间戳
            messages: 消息列表
        """
        try:
            # 传递消息和过滤函数给 ExpressionLearner
            learnt_style = await self.expression_learner.learn_and_store(
                messages=messages,
                person_name_filter=self.contains_person_name
            )
            
            if learnt_style:
                logger.info(f"聊天流 {self.chat_name} 表达学习完成")
            else:
                logger.debug(f"聊天流 {self.chat_name} 表达学习未获得有效结果")
        except Exception as e:
            logger.error(f"为聊天流 {self.chat_name} 触发表达学习失败: {e}")
            import traceback
            traceback.print_exc()
    
    async def _trigger_jargon_extraction(
        self, 
        timestamp_start: float, 
        timestamp_end: float, 
        messages: List[Any]
    ) -> None:
        """
        触发 jargon 提取，使用指定的消息列表
        
        Args:
            timestamp_start: 开始时间戳
            timestamp_end: 结束时间戳
            messages: 消息列表
        """
        try:
            # 传递消息和过滤函数给 JargonMiner
            await self.jargon_miner.run_once(
                messages=messages,
                person_name_filter=self.contains_person_name
            )
            
        except Exception as e:
            logger.error(f"为聊天流 {self.chat_name} 触发黑话提取失败: {e}")
            import traceback
            traceback.print_exc()
    
    def _update_person_list(self, messages: List[Any]) -> None:
        """
        从消息中提取人物信息并更新人物列表
        
        Args:
            messages: 消息列表
        """
        for msg in messages:
            # 获取消息发送者信息
            # 消息对象可能是 DatabaseMessages，它有 user_info 属性
            if hasattr(msg, 'user_info'):
                # DatabaseMessages 类型
                user_info = msg.user_info
                user_id = getattr(user_info, 'user_id', None) or ''
                user_platform = getattr(user_info, 'platform', None) or ''
                user_nickname = getattr(user_info, 'user_nickname', None) or ''
                user_cardname = getattr(user_info, 'user_cardname', None)
            else:
                # 直接属性访问
                user_id = getattr(msg, 'user_id', None) or ''
                user_platform = getattr(msg, 'user_platform', None) or ''
                user_nickname = getattr(msg, 'user_nickname', None) or ''
                user_cardname = getattr(msg, 'user_cardname', None)
            
            msg_time = getattr(msg, 'time', time.time())
            
            # 检查必要信息
            if not user_id or not user_platform:
                continue
            
            # 获取 person_name
            try:
                person = Person(platform=user_platform, user_id=str(user_id))
                person_name = person.person_name or user_nickname or (user_cardname if user_cardname else "未知用户")
            except Exception as e:
                logger.info(f"获取person_name失败: {e}, 使用nickname")
                person_name = user_nickname or (user_cardname if user_cardname else "未知用户")
            
            # 生成唯一key
            unique_key = f"{user_platform}:{user_id}"
            
            # 如果已存在，更新最后发言时间
            if unique_key in self._person_list:
                self._person_list[unique_key].last_seen_time = msg_time
                # 移动到末尾（表示最近活跃）
                self._person_list.move_to_end(unique_key)
            else:
                # 如果超过最大数量，移除最早的（最前面的）
                if len(self._person_list) >= self._max_person_count:
                    oldest_key = next(iter(self._person_list))
                    del self._person_list[oldest_key]
                    logger.info(f"人物列表已满，移除最早的人物: {oldest_key}")
                
                # 添加新人物
                person_info = PersonInfo(
                    user_id=str(user_id),
                    user_platform=user_platform,
                    user_nickname=user_nickname or "",
                    user_cardname=user_cardname,
                    person_name=person_name,
                    last_seen_time=msg_time
                )
                self._person_list[unique_key] = person_info
                logger.info(f"添加新人物到列表: {unique_key}, person_name={person_name}")
    
    def contains_person_name(self, content: str) -> bool:
        """
        检查内容是否包含任何参与聊天的人物的名称或昵称
        
        Args:
            content: 要检查的内容
            
        Returns:
            bool: 如果包含任何人物名称或昵称，返回True
        """
        if not content or not self._person_list:
            return False
        
        content_lower = content.strip().lower()
        if not content_lower:
            return False
        
        # 检查所有人物
        for person_info in self._person_list.values():
            # 检查 person_name
            if person_info.person_name:
                person_name_lower = person_info.person_name.strip().lower()
                if person_name_lower and person_name_lower in content_lower:
                    logger.debug(f"内容包含person_name: {person_info.person_name} in {content}")
                    return True
            
            # 检查 user_nickname
            if person_info.user_nickname:
                nickname_lower = person_info.user_nickname.strip().lower()
                if nickname_lower and nickname_lower in content_lower:
                    logger.debug(f"内容包含nickname: {person_info.user_nickname} in {content}")
                    return True
            
            # 检查 user_cardname（群昵称）
            if person_info.user_cardname:
                cardname_lower = person_info.user_cardname.strip().lower()
                if cardname_lower and cardname_lower in content_lower:
                    logger.debug(f"内容包含cardname: {person_info.user_cardname} in {content}")
                    return True
        
        return False


class MessageRecorderManager:
    """MessageRecorder 管理器"""
    
    def __init__(self) -> None:
        self._recorders: dict[str, MessageRecorder] = {}
    
    def get_recorder(self, chat_id: str) -> MessageRecorder:
        """获取或创建指定 chat_id 的 MessageRecorder"""
        if chat_id not in self._recorders:
            self._recorders[chat_id] = MessageRecorder(chat_id)
        return self._recorders[chat_id]


# 全局管理器实例
recorder_manager = MessageRecorderManager()


async def extract_and_distribute_messages(chat_id: str) -> None:
    """
    统一的消息提取和分发入口函数
    
    Args:
        chat_id: 聊天流ID
    """
    recorder = recorder_manager.get_recorder(chat_id)
    await recorder.extract_and_distribute()

