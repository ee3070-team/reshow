#pragma once
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// 200x200 panel, 6x8 built-in GFX font. One shared layout for validation/drawing.
// No replacement glyphs or silently truncated text can be acknowledged.
constexpr uint8_t LABEL_COLUMNS = 31;
constexpr uint8_t LABEL_ROWS = 22;
struct LabelLayout {
  char lines[LABEL_ROWS][LABEL_COLUMNS + 1] = {};
  uint8_t used = 0;
  uint8_t ruleAfter = 0;
};

inline bool labelAscii(const char *text, size_t maximum, bool required) {
  if (!text) return false;
  size_t n = strlen(text);
  if (n > maximum || (required && n == 0)) return false;
  bool visible = false;
  for (size_t i = 0; i < n; ++i) {
    unsigned char c = text[i];
    if (c != '\n' && (c < 32 || c > 126)) return false;
    if (c > 32) visible = true;
  }
  return !required || visible;
}

inline bool formatMegaLabel(const char *title, const char *collection,
                            const char *description, char *out, size_t capacity) {
  if (!title || !collection || !description || !out || capacity == 0 ||
      !*title || !*description) return false;
  const char *fields[] = {title, collection, description};
  for (const char *text : fields) {
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p)
      if (*p < 32 || *p > 126) return false;
  }
  int n = *collection
    ? snprintf(out, capacity, "%s | %s | %s", title, collection, description)
    : snprintf(out, capacity, "%s | %s", title, description);
  return n > 0 && n <= 180 && (size_t)n < capacity;
}

inline bool appendLabelText(LabelLayout &layout, const char *text) {
  while (*text) {
    if (layout.used >= LABEL_ROWS) return false;
    size_t n = 0;
    while (text[n] && text[n] != '\n' && n < LABEL_COLUMNS) ++n;
    size_t consumed = n;
    if (text[n] == '\n') ++consumed;
    else if (n == LABEL_COLUMNS && text[n] && text[n] != ' ') {
      // Prefer whole words; long identifiers are wrapped at a character boundary.
      size_t split = n;
      while (split > 0 && text[split - 1] != ' ') --split;
      if (split > 0) { n = split - 1; consumed = split; }
    } else if (text[n] == ' ') ++consumed;
    memcpy(layout.lines[layout.used], text, n);
    layout.lines[layout.used++][n] = '\0';
    text += consumed;
  }
  return true;
}

inline bool makeLabelLayout(const char *title, const char *collection,
                            const char *description, LabelLayout &layout) {
  layout = LabelLayout{};
  if (!labelAscii(title, 100, true) || !labelAscii(collection, 120, false) ||
      !labelAscii(description, 2000, true)) return false;
  if (!appendLabelText(layout, title)) return false;
  if (*collection && !appendLabelText(layout, collection)) return false;
  layout.ruleAfter = layout.used;
  // Reserve a blank row for the separating rule.
  if (layout.used >= LABEL_ROWS) return false;
  ++layout.used;
  return appendLabelText(layout, description);
}
