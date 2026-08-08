import { describe, expect, it } from 'vitest'
import { preprocessYoutubeLinks, youtubeIdFromUrl } from './components/MarkdownRenderer'

describe('youtubeIdFromUrl', () => {
  it('extracts ids from the supported YouTube URL shapes', () => {
    expect(youtubeIdFromUrl('https://www.youtube.com/watch?v=dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
    expect(youtubeIdFromUrl('https://youtube.com/watch?v=dQw4w9WgXcQ&t=42s')).toBe('dQw4w9WgXcQ')
    expect(youtubeIdFromUrl('https://m.youtube.com/watch?v=dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
    expect(youtubeIdFromUrl('https://youtu.be/dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
    expect(youtubeIdFromUrl('https://youtu.be/dQw4w9WgXcQ?si=abc')).toBe('dQw4w9WgXcQ')
    expect(youtubeIdFromUrl('https://www.youtube.com/shorts/dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
    expect(youtubeIdFromUrl('https://www.youtube.com/embed/dQw4w9WgXcQ')).toBe('dQw4w9WgXcQ')
  })

  it('rejects non-YouTube hosts and malformed ids', () => {
    expect(youtubeIdFromUrl('https://example.com/watch?v=dQw4w9WgXcQ')).toBeNull()
    expect(youtubeIdFromUrl('https://youtube.com/channel/UCabc')).toBeNull()
    expect(youtubeIdFromUrl('https://youtu.be/')).toBeNull()
    expect(youtubeIdFromUrl('https://youtu.be/dQw4w9WgXcQ.')).toBeNull() // trailing punctuation
    expect(youtubeIdFromUrl('https://youtu.be/abc1234567')).toBeNull() // not 11 chars
    expect(youtubeIdFromUrl('not a url')).toBeNull()
  })
})

describe('preprocessYoutubeLinks', () => {
  it('autolinks bare YouTube URLs so they render as preview cards', () => {
    expect(preprocessYoutubeLinks('see https://youtu.be/dQw4w9WgXcQ now'))
      .toBe('see <https://youtu.be/dQw4w9WgXcQ> now')
  })

  it('does not touch URLs already inside markdown link syntax', () => {
    const md = '[watch](https://youtu.be/dQw4w9WgXcQ)'
    expect(preprocessYoutubeLinks(md)).toBe(md)
  })

  it('leaves non-YouTube bare URLs alone', () => {
    expect(preprocessYoutubeLinks('go to https://example.com/x')).toBe('go to https://example.com/x')
  })

  it('never rewrites URLs inside fenced or inline code', () => {
    const fenced = '```\nhttps://youtu.be/dQw4w9WgXcQ\n```'
    expect(preprocessYoutubeLinks(fenced)).toBe(fenced)
    const inline = 'use `https://youtu.be/dQw4w9WgXcQ` here'
    expect(preprocessYoutubeLinks(inline)).toBe(inline)
  })
})
