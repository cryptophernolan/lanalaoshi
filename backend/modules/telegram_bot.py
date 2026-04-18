"""
Telegram Bot — mobile alerts + inline controls.

Features:
1. Signal alerts với inline buttons (approve/reject)
2. Position updates realtime
3. Kill switch từ phone
4. /stats command cho daily summary
5. /positions để xem open positions
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional, Callable
import httpx

from modules.schemas import TradeSignal, Position, ClosedTrade, Side, SignalStrength
from config.settings import config

logger = logging.getLogger(__name__)


class TelegramBot:
    """
    Minimal Telegram bot không cần dependency nặng (python-telegram-bot).
    Chỉ dùng httpx gọi trực tiếp Bot API.
    """
    
    def __init__(self):
        self.token = config.telegram_bot_token
        self.chat_id = config.telegram_chat_id
        self.enabled = bool(self.token and self.chat_id)
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        self._client = httpx.AsyncClient(timeout=10.0)
        self._last_update_id: Optional[int] = None
        
        # Callbacks để bot chính đăng ký
        self.on_kill_switch: Optional[Callable] = None
        self.on_reset: Optional[Callable] = None
        self.on_close_all: Optional[Callable] = None
        self.on_stats_request: Optional[Callable] = None
        self.on_positions_request: Optional[Callable] = None
        self.on_approve_signal: Optional[Callable] = None
        self.on_reject_signal: Optional[Callable] = None
    
    async def send(self, text: str, parse_mode: str = "HTML", reply_markup: dict = None):
        if not self.enabled:
            return
        try:
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            
            r = await self._client.post(f"{self.base_url}/sendMessage", json=payload)
            r.raise_for_status()
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")
    
    async def answer_callback(self, callback_id: str, text: str = ""):
        try:
            await self._client.post(
                f"{self.base_url}/answerCallbackQuery",
                json={"callback_query_id": callback_id, "text": text},
            )
        except Exception as e:
            logger.warning(f"Callback answer failed: {e}")
    
    # ============ MESSAGE FORMATTERS ============
    
    def _format_signal(self, signal: TradeSignal) -> str:
        side_emoji = "🟢" if signal.side == Side.LONG else "🔴"
        strength_emoji = {
            SignalStrength.STRONG: "🔥🔥🔥",
            SignalStrength.MEDIUM: "🔥🔥",
            SignalStrength.WEAK: "🔥",
        }.get(signal.strength, "")
        
        text = (
            f"{side_emoji} <b>SIGNAL: {signal.side.value} {signal.symbol}</b> {strength_emoji}\n\n"
            f"<b>Entry:</b> <code>{signal.entry_price:.4f}</code>\n"
            f"<b>Stop Loss:</b> <code>{signal.stop_loss:.4f}</code>\n"
            f"<b>Take Profit:</b> <code>{signal.take_profit:.4f}</code>\n"
            f"<b>R:R:</b> 1:{signal.risk_reward_ratio:.1f}\n"
            f"<b>Size:</b> ${signal.suggested_size_usdt:.0f} @ {signal.leverage}x\n"
            f"<b>Confidence:</b> {signal.confidence*100:.0f}%\n\n"
            f"<i>{signal.reasoning}</i>\n\n"
            f"<code>ID: {signal.signal_id}</code>"
        )
        return text
    
    def _signal_keyboard(self, signal_id: str) -> dict:
        return {
            "inline_keyboard": [[
                {"text": "✅ Execute", "callback_data": f"approve:{signal_id}"},
                {"text": "❌ Skip", "callback_data": f"reject:{signal_id}"},
            ]]
        }
    
    def _format_position_opened(self, p: Position) -> str:
        side_emoji = "🟢" if p.side == Side.LONG else "🔴"
        return (
            f"{side_emoji} <b>POSITION OPENED</b>\n\n"
            f"<b>{p.symbol}</b> {p.side.value}\n"
            f"Entry: <code>{p.entry_price:.4f}</code>\n"
            f"SL: <code>{p.stop_loss:.4f}</code>\n"
            f"TP: <code>{p.take_profit:.4f}</code>\n"
            f"Size: ${p.size_usdt:.0f} @ {p.leverage}x"
        )
    
    def _format_position_closed(self, t: ClosedTrade) -> str:
        win = t.realized_pnl_usdt > 0
        emoji = "🎯" if win else "🛑"
        reason_emoji = {"TP": "✅", "SL": "🛑", "TRAILING": "📈", "MANUAL": "✋"}.get(
            t.exit_reason, "•"
        )
        return (
            f"{emoji} <b>CLOSED: {t.symbol}</b> {reason_emoji}\n\n"
            f"{t.side.value}: <code>{t.entry_price:.4f}</code> → <code>{t.exit_price:.4f}</code>\n"
            f"PnL: <b>${t.realized_pnl_usdt:+.2f}</b> ({t.realized_pnl_pct:+.2f}%)\n"
            f"Fees: ${t.fees_usdt:.2f}\n"
            f"Exit: {t.exit_reason}"
        )
    
    def _format_stats(self, stats: dict) -> str:
        pnl = stats.get("total_pnl_usdt", 0)
        pnl_emoji = "📈" if pnl >= 0 else "📉"
        return (
            f"{pnl_emoji} <b>BOT STATS</b>\n\n"
            f"Total PnL: <b>${pnl:+.2f}</b>\n"
            f"Trades: {stats.get('total_trades', 0)}\n"
            f"Win Rate: {stats.get('win_rate', 0)*100:.1f}%\n"
            f"Profit Factor: {stats.get('profit_factor', 0):.2f}\n"
            f"Avg Win: ${stats.get('avg_win_usdt', 0):.2f}\n"
            f"Avg Loss: ${stats.get('avg_loss_usdt', 0):.2f}\n"
            f"Open Positions: {stats.get('open_positions', 0)}"
        )
    
    def _format_kill_switch_triggered(self, reason: str) -> str:
        return (
            f"🚨🚨🚨 <b>KILL SWITCH TRIGGERED</b>\n\n"
            f"Reason: <i>{reason}</i>\n"
            f"All positions closed, trading halted.\n\n"
            f"Use /reset to resume (after reviewing)."
        )
    
    # ============ PUBLIC METHODS ============
    
    async def alert_signal(self, signal: TradeSignal, require_approval: bool = False):
        """Gửi signal alert. require_approval=True sẽ thêm inline buttons."""
        if not self.enabled:
            return
        text = self._format_signal(signal)
        keyboard = self._signal_keyboard(signal.signal_id) if require_approval else None
        await self.send(text, reply_markup=keyboard)
    
    async def alert_position_opened(self, position: Position):
        await self.send(self._format_position_opened(position))
    
    async def alert_position_closed(self, trade: ClosedTrade):
        await self.send(self._format_position_closed(trade))
    
    async def alert_kill_switch(self, reason: str):
        await self.send(self._format_kill_switch_triggered(reason))
    
    async def send_stats(self, stats: dict):
        await self.send(self._format_stats(stats))
    
    async def send_positions_list(self, positions: list[Position]):
        if not positions:
            await self.send("📭 No open positions.")
            return
        text = "<b>📊 OPEN POSITIONS</b>\n\n"
        for p in positions:
            pnl_emoji = "📈" if p.unrealized_pnl_usdt >= 0 else "📉"
            text += (
                f"{pnl_emoji} <b>{p.symbol}</b> {p.side.value}\n"
                f"   Entry: {p.entry_price:.4f} | Mark: {p.current_price:.4f}\n"
                f"   PnL: <b>${p.unrealized_pnl_usdt:+.2f}</b> ({p.unrealized_pnl_pct:+.2f}%)\n\n"
            )
        await self.send(text)
    
    # ============ COMMAND HANDLING ============
    
    async def _handle_update(self, update: dict):
        # Callback query (button click)
        if "callback_query" in update:
            cq = update["callback_query"]
            data = cq.get("data", "")
            cb_id = cq["id"]
            
            if data.startswith("approve:"):
                signal_id = data.split(":", 1)[1]
                if self.on_approve_signal:
                    await self.on_approve_signal(signal_id)
                await self.answer_callback(cb_id, "✅ Approved")
            elif data.startswith("reject:"):
                signal_id = data.split(":", 1)[1]
                if self.on_reject_signal:
                    await self.on_reject_signal(signal_id)
                await self.answer_callback(cb_id, "❌ Rejected")
            return
        
        # Text message
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        
        # Only respond to configured chat
        if str(msg.get("chat", {}).get("id")) != str(self.chat_id):
            return
        
        text = msg.get("text", "").strip().lower()
        
        if text == "/start" or text == "/help":
            await self.send(
                "<b>🤖 OI Divergence Bot</b>\n\n"
                "Commands:\n"
                "/stats - Trading statistics\n"
                "/positions - Open positions\n"
                "/kill [reason] - Emergency stop\n"
                "/reset - Reset kill switch\n"
                "/closeall - Close all positions\n"
                "/status - Bot status\n"
            )
        elif text == "/stats":
            if self.on_stats_request:
                await self.on_stats_request()
        elif text == "/positions":
            if self.on_positions_request:
                await self.on_positions_request()
        elif text.startswith("/kill"):
            reason = text[5:].strip() or "Telegram command"
            if self.on_kill_switch:
                await self.on_kill_switch(reason)
            await self.send(f"🚨 Kill switch triggered: {reason}")
        elif text == "/reset":
            if self.on_reset:
                await self.on_reset()
            await self.send("♻️ Kill switch reset.")
        elif text == "/closeall":
            if self.on_close_all:
                await self.on_close_all()
            await self.send("✋ Closing all positions...")
        elif text == "/status":
            await self.send(
                f"<b>Status</b>\n"
                f"Testnet: {config.binance.testnet}\n"
                f"Dry run: {config.executor.dry_run}"
            )
    
    async def run_polling(self):
        """Long polling cho Telegram updates."""
        if not self.enabled:
            logger.info("Telegram disabled (no token/chat_id)")
            return
        
        logger.info("Telegram bot started")
        await self.send("🤖 <b>OI Divergence Bot ONLINE</b>\n\nReady to trade. /help for commands.")
        
        while True:
            try:
                params = {"timeout": 30}
                if self._last_update_id is not None:
                    params["offset"] = self._last_update_id + 1
                
                r = await self._client.get(
                    f"{self.base_url}/getUpdates",
                    params=params,
                    timeout=35.0,
                )
                r.raise_for_status()
                data = r.json()
                
                for update in data.get("result", []):
                    self._last_update_id = update["update_id"]
                    try:
                        await self._handle_update(update)
                    except Exception as e:
                        logger.error(f"Update handle error: {e}", exc_info=True)
            except Exception as e:
                logger.warning(f"Telegram polling error: {e}")
                await asyncio.sleep(5)
    
    async def close(self):
        await self._client.aclose()
