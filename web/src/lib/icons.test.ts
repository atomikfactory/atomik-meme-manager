import { describe, expect, it } from 'vitest';
import { resolveIcon } from './icons';

describe('resolveIcon', () => {
  it('maps every known backend icon name to its glyph', () => {
    expect(resolveIcon('heart')).toBe('♥');
    expect(resolveIcon('clock')).toBe('◷');
    expect(resolveIcon('eye')).toBe('◉');
    expect(resolveIcon('gif')).toBe('GIF');
    expect(resolveIcon('video')).toBe('▶');
    expect(resolveIcon('image')).toBe('▣');
    expect(resolveIcon('warning')).toBe('⚠');
    expect(resolveIcon('copy')).toBe('⧉');
    expect(resolveIcon('folder')).toBe('▤');
    expect(resolveIcon('star')).toBe('★');
    expect(resolveIcon('shuffle')).toBe('⤮');
    expect(resolveIcon('tag')).toBe('#');
  });

  it('is case-insensitive and trims whitespace', () => {
    expect(resolveIcon('Heart')).toBe('♥');
    expect(resolveIcon('  HEART  ')).toBe('♥');
    expect(resolveIcon('GiF')).toBe('GIF');
  });

  it('passes through a short value that already looks like a glyph/emoji', () => {
    expect(resolveIcon('★')).toBe('★'); // already a glyph, not a known name
    expect(resolveIcon('🔥')).toBe('🔥');
    expect(resolveIcon('42')).toBe('42');
  });

  it('falls back to a bullet for an unrecognised word', () => {
    expect(resolveIcon('sparkles')).toBe('•');
    expect(resolveIcon('unknown-icon-name')).toBe('•');
  });

  it('returns null for missing/empty icons so callers can supply their own default', () => {
    expect(resolveIcon(null)).toBeNull();
    expect(resolveIcon(undefined)).toBeNull();
    expect(resolveIcon('')).toBeNull();
    expect(resolveIcon('   ')).toBeNull();
  });
});
