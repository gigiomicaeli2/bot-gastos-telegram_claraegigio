import re
import sqlite3
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)

# Configuração do Banco de Dados SQLite local
conn = sqlite3.connect("gastos.db", check_same_thread=False)
cursor = conn.cursor()

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

# Mapeamento dos meses para português
MESES_PT = {
    "01": "Janeiro", "02": "Fevereiro", "03": "Março", "04": "Abril",
    "05": "Maio", "06": "Junho", "07": "Julho", "08": "Agosto",
    "09": "Setembro", "10": "Outubro", "11": "Novembro", "12": "Dezembro"
}


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "👋 **Bot de Gastos Ativo!**\n\n"
        "• Registrar gasto: Mande mensagem com o gasto (ex: `Mercado 150.50`)\n"
        "• `/dia` - Ver fechamento do dia de hoje\n"
        "• `/mes` - Ver gastos detalhados do mês atual por pessoa\n"
        "• `/mes MM/AAAA` - Ver gastos de um mês específico (ex: `/mes 07/2026`)\n"
        "• `/mesanterior` - Ver gastos do mês passado\n"
        "• `/ano` - Ver o resumo de gastos do ano atual\n"
        "• `/ano AAAA` - Ver o resumo de um ano específico (ex: `/ano 2025`)\n"
        "• `/desfazer` - Responda a qualquer mensagem de gasto para apagá-lo (ou mande direto para apagar o último)"
    )
    await update.message.reply_text(msg, parse_mode='Markdown')


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

        cursor.execute(
            "SELECT SUM(valor) FROM gastos WHERE user_id = ? AND data LIKE ?",
            (user_id, f"{mes_atual}%"),
        )
        subtotal_anterior = cursor.fetchone()[0] or 0.0
        subtotal_usuario_mes = subtotal_anterior + valor

        resposta_texto = (
            f"✅ Gasto anotado, {user_name}!\n"
            f"📌 {descricao}: R$ {valor:.2f}\n"
            f"📊 Seu subtotal neste mês: R$ {subtotal_usuario_mes:.2f}\n\n"
            f"💡 *Para apagar este gasto, responda a esta mensagem com /desfazer.*"
        )

        msg_enviada = await update.message.reply_text(resposta_texto, parse_mode="Markdown")

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


async def desfazer_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = str(user.id)

    # Modo 1: Se for uma resposta (Reply) a uma mensagem do Bot ou do usuário
    if update.message.reply_to_message:
        msg_respondida_id = update.message.reply_to_message.message_id

        cursor.execute(
            "SELECT id, descricao, valor FROM gastos WHERE (message_id = ? OR message_id = ?) AND user_id = ?",
            (msg_respondida_id, msg_respondida_id - 1, user_id),
        )
        gasto = cursor.fetchone()

        if gasto:
            gasto_id, descricao, valor = gasto
            cursor.execute("DELETE FROM gastos WHERE id = ?", (gasto_id,))
            conn.commit()
            await update.message.reply_text(
                f"🗑️ Gasto apagado com sucesso!\n❌ Removido: {descricao} (R$ {valor:.2f})"
            )
            return
        else:
            await update.message.reply_text("⚠️ Não encontrei nenhum gasto associado a essa mensagem.")
            return

    # Modo 2: Apagar o último gasto do próprio usuário
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
        f"🗑️ Último gasto apagado com sucesso!\n❌ Removido: {descricao} (R$ {valor:.2f})"
    )


async def relatorio_dia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hoje = datetime.now().strftime("%Y-%m-%d")
    cursor.execute(
        "SELECT user_name, SUM(valor) FROM gastos WHERE data = ? GROUP BY user_id",
        (hoje,),
    )
    resultados = cursor.fetchall()

    if not resultados:
        await update.message.reply_text("Nenhum gasto registrado hoje até o momento.")
        return

    texto = f"🌙 **Fechamento do Dia ({datetime.now().strftime('%d/%m/%Y')}):**\n\n"
    total_dia = 0.0
    for nome, soma in resultados:
        texto += f"• {nome}: R$ {soma:.2f}\n"
        total_dia += soma
    texto += f"\n💰 **Total gasto pelo casal hoje:** R$ {total_dia:.2f}"

    await update.message.reply_text(texto, parse_mode="Markdown")


async def gerar_relatorio_mes_detalhado(user_id_solicitante, mes_alvo, titulo_mes):
    cursor.execute('''
        SELECT data, user_name, descricao, valor, user_id
        FROM gastos 
        WHERE data LIKE ?
        ORDER BY id ASC
    ''', (f"{mes_alvo}%",))
    
    resultados = cursor.fetchall()

    if not resultados:
        return f"📊 Nenhum gasto registrado em **{titulo_mes}**!"

    mensagem = f"📊 **Gastos Detalhados ({titulo_mes}):**\n\n"
    totais_por_usuario = {}
    total_geral = 0.0

    for data_str, nome, desc, valor, uid in resultados:
        try:
            data_dt = datetime.strptime(data_str, '%Y-%m-%d')
            data_fmt = data_dt.strftime('%d/%m')
        except ValueError:
            data_fmt = data_str
        
        nome_exibicao = nome if nome else ("Você" if str(uid) == str(user_id_solicitante) else "Outro")
        mensagem += f"• `{data_fmt}` ({nome_exibicao}) - **{desc}**: R$ {valor:.2f}\n"
        
        totais_por_usuario[nome_exibicao] = totais_por_usuario.get(nome_exibicao, 0.0) + valor
        total_geral += valor

    mensagem += "\n👥 **Resumo por Pessoa:**\n"
    for nome, total_user in totais_por_usuario.items():
        mensagem += f"• **{nome}**: R$ {total_user:.2f}\n"

    mensagem += f"\n💰 **Total Geral:** R$ {total_geral:.2f}"
    return mensagem


async def relatorio_mes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    if context.args:
        try:
            mes_ano_input = context.args[0]
            partes = mes_ano_input.split('/')
            mes_str, ano_str = partes[0].zfill(2), partes[1]
            mes_alvo = f"{ano_str}-{mes_str}"
            titulo_mes = f"{mes_str}/{ano_str}"
        except (IndexError, ValueError):
            await update.message.reply_text("⚠️ Formato inválido! Use: `/mes MM/AAAA` (ex: `/mes 07/2026`)", parse_mode='Markdown')
            return
    else:
        now = datetime.now()
        mes_alvo = now.strftime('%Y-%m')
        titulo_mes = now.strftime('%m/%Y')

    resposta = await gerar_relatorio_mes_detalhado(user_id, mes_alvo, titulo_mes)
    await update.message.reply_text(resposta, parse_mode='Markdown')


async def relatorio_mes_anterior(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    
    hoje = datetime.now()
    primeiro_dia_mes_atual = hoje.replace(day=1)
    ultimo_dia_mes_anterior = primeiro_dia_mes_atual - timedelta(days=1)
    
    mes_alvo = ultimo_dia_mes_anterior.strftime('%Y-%m')
    titulo_mes = ultimo_dia_mes_anterior.strftime('%m/%Y')

    resposta = await gerar_relatorio_mes_detalhado(user_id, mes_alvo, titulo_mes)
    await update.message.reply_text(resposta, parse_mode='Markdown')


async def relatorio_ano(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Verifica se o usuário informou um ano específico (ex: /ano 2025)
    if context.args:
        ano_solicitado = context.args[0]
        if not ano_solicitado.isdigit() or len(ano_solicitado) != 4:
            await update.message.reply_text("⚠️ Formato de ano inválido! Use: `/ano AAAA` (ex: `/ano 2026`)", parse_mode='Markdown')
            return
        ano_alvo = ano_solicitado
    else:
        ano_alvo = datetime.now().strftime('%Y')

    cursor.execute('''
        SELECT strftime('%m', data) as mes, SUM(valor)
        FROM gastos
        WHERE data LIKE ?
        GROUP BY mes
        ORDER BY mes ASC
    ''', (f"{ano_alvo}%",))

    resultados = cursor.fetchall()

    if not resultados:
        await update.message.reply_text(f"📅 Nenhum gasto registrado no ano de {ano_alvo}!")
        return

    mensagem = f"📅 **Gastos Acumulados por Mês ({ano_alvo}):**\n\n"
    total_ano = 0.0

    for mes_num, total_mes in resultados:
        nome_mes = MESES_PT.get(mes_num, mes_num)
        mensagem += f"• **{nome_mes}**: R$ {total_mes:.2f}\n"
        total_ano += total_mes

    mensagem += f"\n💵 **Total Geral em {ano_alvo}:** R$ {total_ano:.2f}"

    await update.message.reply_text(mensagem, parse_mode='Markdown')


def main():
    TOKEN = "8749873142:AAHra0Uxo3j_mg1pxqXfufCy2NrjBC6kKxA"

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("dia", relatorio_dia))
    app.add_handler(CommandHandler("mes", relatorio_mes))
    app.add_handler(CommandHandler("mesanterior", relatorio_mes_anterior))
    app.add_handler(CommandHandler("ano", relatorio_ano))
    app.add_handler(CommandHandler("desfazer", desfazer_gasto))

    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), processar_mensagem)
    )

    print("Bot rodando no Render...")
    app.run_polling()


if __name__ == "__main__":
    main()
