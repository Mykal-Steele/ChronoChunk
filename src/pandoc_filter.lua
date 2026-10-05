-- Keeps AI-written Markdown from pulling in anything the bot did not create.
-- Only images named in ALLOWED_IMAGES (colon separated) survive, and raw
-- HTML or TeX is dropped. Without this a document could embed local files
-- or fetch URLs from inside the server.

local allowed = {}
for name in string.gmatch(os.getenv("ALLOWED_IMAGES") or "", "[^:]+") do
  allowed[name] = true
end

function Image(el)
  if allowed[el.src] then
    return el
  end
  return pandoc.Str("[image removed]")
end

function RawBlock()
  return {}
end

function RawInline()
  return {}
end
