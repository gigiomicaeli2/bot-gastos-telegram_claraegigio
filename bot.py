import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import re
import sqlite3
from datetime import datetime
import nest_asyncio
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)

# Aplica a correção para o Google Colab rodar sem conflito
nest_asyncio.apply()

# Configuração do Banco de Dados SQLite local
conn = sqlite3.connect("gastos.db", check_same_thread=False)
cursor = conn.cursor()

# Atualiza a tabela para salvar também o ID da mensagem enviada pelo bot
cursor.execute(
    """
CREATE TABLE IF NOT EXISTS gastos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT,
    user_name TEXT,
    descricao TEXT,
    valor REAL,
    data TEXT,
    message_id INTEGER
)
"""
)
conn.commit()


# Função para extrair valor e descrição da mensagem
def extrair_gasto(texto):
    match = re.search(r"(\d+[\.,]?\d*)", texto)
    if not match:
        return None, None

    valor_str = match.group(1).replace(",", ".")
    valor = float(valor_str)

    descricao = texto.replace(match.group(1), "").strip()
    descricao = re.sub(
        r"(reais|real|r\$)", "", descricao, flags=re.IGNORECASE
    ).strip()

    return descricao if descricao else "Gasto geral", valor


# Processa as mensagens de gastos enviadas no grupo
async def processar_mensagem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    texto = update.message.text
    user = update.effective_user
    user_id = str(user.id)
    user_name = user.first_name

    descricao, valor = extrair_gasto(texto)
    if valor is not None:
        hoje = datetime.now().strftime("%Y-%m-%d")
        mes_atual = datetime.now().strftime("%Y-%m")

        # Envia a resposta e captura o ID da mensagem do bot
        mes_atual_str = datetime.now().strftime("%Y-%m")
        cursor.execute(
            "SELECT SUM(valor) FROM gastos WHERE user_id = ? AND data LIKE ?",
            (user_id, f"{mes_atual}%"),
        )
        subtotal_anterior = cursor.fetchone()[0] or 0.0
        subtotal_usuario_mes = subtotal_anterior + valor

        resposta_texto = (
            f"✅ Gasto anotado, {user_name}!\n"
            f"📌 {descricao}: R$ {valor:.2f}\n"
            f"📊 Seu subtotal neste mês: R$ {subtotal_usuario_mes:.2f}"
        )

        msg_enviada = await update.message.reply_text(resposta_texto)

        # Salva no banco incluindo o ID da mensagem gerada pelo bot
        cursor.execute(
            "INSERT INTO gastos (user_id, user_name, descricao, valor, data, message_id) VALUES (?, ?, ?, ?, ?, ?)",
            (
                user_id,
                user_name,
                descricao,
                valor,
                hoje,
                msg_enviada.message_id,
            ),
        )
        conn.commit()


# Comando para desfazer (apoiando resposta à mensagem ou o último padrão)
async def desfazer_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)

    # Verifica se o usuário usou o recurso de "Responder" a uma mensagem do bot
    if (
        update.message.reply_to_message
        and update.message.reply_to_message.from_user.id
        == context.bot.id
    ):
        msg_respondida_id = update.message.reply_to_message.message_id

        # Procura o gasto associado àquela mensagem exata do bot
        cursor.execute(
            "SELECT id, descricao, valor FROM gastos WHERE message_id = ? AND user_id = ?",
            (msg_respondida_id, user_id),
        )
        gasto = cursor.fetchone()

        if gasto:
            gasto_id, descricao, valor = gasto
            cursor.execute("DELETE FROM gastos WHERE id = ?", (gasto_id,))
            conn.commit()
            await update.message.reply_text(
                f"🗑️ Gasto apagado com sucesso!\n"
                f"❌ Removido: {descricao} (R$ {valor:.2f})"
            )
            return
        else:
            await update.message.reply_text(
                "Não encontrei nenhum registro de gasto vinculado a esta mensagem específica."
            )
            return

    # Comportamento padrão caso não responda nenhuma mensagem específica: apaga o último
    cursor.execute(
        "SELECT id, descricao, valor FROM gastos WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    ultimo = cursor.fetchone()

    if not ultimo:
        await update.message.reply_text(
            "Você não tem nenhum gasto registrado para apagar."
        )
        return

    gasto_id, descricao, valor = ultimo
    cursor.execute("DELETE FROM gastos WHERE id = ?", (gasto_id,))
    conn.commit()

    await update.message.reply_text(
        f"🗑️ Último gasto apagado com sucesso!\n"
        f"❌ Removido: {descricao} (R$ {valor:.2f})"
    )


# Relatório do dia (comando /dia)
async def relatorio_dia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hoje = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT user_name, SUM(valor) FROM gastos WHERE data = ? GROUP BY user_id",
        (hoje,),
    )
    resultados = cursor.fetchall()

    if not resultados:
        await update.message.reply_text(
            "Nenhum gasto registrado hoje até o momento."
        )
        return

    texto = f"🌙 **Fechamento do Dia ({datetime.now().strftime('%d/%m/%Y')}):**\n"
    total_dia = 0
    for nome, soma in resultados:
        texto += f"- {nome}: R$ {soma:.2f}\n"
        total_dia += soma
    texto += f"\n💰 **Total gasto pelo casal hoje:** R$ {total_dia:.2f}"

    await update.message.reply_text(texto, parse_mode="Markdown")


# Relatório do mês (comando /mes)
async def relatorio_mes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mes_atual = datetime.now().strftime("%Y-%m")
    cursor.execute(
        "SELECT user_name, SUM(valor) FROM gastos WHERE data LIKE ? GROUP BY user_id",
        (f"{mes_atual}%",),
    )
    resultados = cursor.fetchall()

    if not resultados:
        await update.message.reply_text("Nenhum gasto registrado neste mês.")
        return

    texto = f"📅 **Fechamento do Mês ({datetime.now().strftime('%m/%Y')}):**\n"
    total_casal = 0
    for nome, soma in resultados:
        texto += f"- Subtotal de {nome}: R$ {soma:.2f}\n"
        total_casal += soma
    texto += f"\n🔥 **Gasto Total do Casal no Mês:** R$ {total_casal:.2f}"

    await update.message.reply_text(texto, parse_mode="Markdown")


def main():
    TOKEN = "8749873142:AAFKUc4l0WNP1lLKfHbi8f19z-USTYFIek8"

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), processar_mensagem)
    )
    app.add_handler(CommandHandler("dia", relatorio_dia))
    app.add_handler(CommandHandler("mes", relatorio_mes))
    app.add_handler(CommandHandler("desfazer", desfazer_gasto))

    print("Bot rodando com desfazer via resposta...")
    app.run_polling()


# --- TRUQUE PARA ENGANAR O RENDER ---
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot esta rodando!")

def keep_alive():
    # O Render exige que usemos a porta que ele fornece, ou a 8080 como padrão
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()
# ------------------------------------

if __name__ == "__main__":
    # 1. Ligando o site falso em segundo plano (isso satisfaz o Render)
    threading.Thread(target=keep_alive, daemon=True).start()
    
    # 2. Ligando o seu bot de verdade
    main()
