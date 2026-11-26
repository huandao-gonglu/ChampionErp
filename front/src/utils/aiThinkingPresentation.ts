/** 仅用于显示；文本标签不改变原生消息、工具执行或历史。 */
export interface AssistantTextSegment {
  kind: 'text' | 'thinking' | 'unseparated' | 'tool-markup'
  text: string
}

export function splitAssistantText(text: string, streaming = false): AssistantTextSegment[] {
  const segments: AssistantTextSegment[] = []
  let rest = text
  while (rest) {
    const prefix = rest.trimStart()
    if (streaming && prefix && '<think>'.startsWith(prefix)) {
      segments.push({ kind: 'unseparated', text: rest })
      break
    }
    // 只处理开头的标签块；正文提及标签或代码示例不做解释。
    if (!prefix.startsWith('<think>')) {
      const toolMarkup = /^<run_code>\s*<code>|^<[｜|]{1,2}DSML[｜|]{1,2}\s*(?:function_calls|invoke)\b|<\/[｜|]{1,2}DSML[｜|]{1,2}\s*(?:function_calls|invoke|parameter|calls)\s*>/i.test(prefix)
      segments.push({ kind: toolMarkup ? 'tool-markup' : 'text', text: rest })
      break
    }
    const end = prefix.indexOf('</think>', '<think>'.length)
    if (end < 0) {
      // 没有可靠分界时保留整段，包括可能混在里面的答复；不猜测语言或标题。
      segments.push({ kind: 'unseparated', text: rest })
      break
    }
    segments.push({ kind: 'thinking', text: prefix.slice('<think>'.length, end) })
    rest = prefix.slice(end + '</think>'.length)
  }
  return segments
}

export function visibleAssistantText(text: string, streaming = false): string {
  return splitAssistantText(text, streaming).filter(segment => segment.kind === 'text')
    .map(segment => segment.text).join('')
}
