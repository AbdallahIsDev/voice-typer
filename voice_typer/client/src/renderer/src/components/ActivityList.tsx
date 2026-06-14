import { useState, useCallback } from 'react'
import { Copy, Check, Trash2, Star } from 'lucide-react'
import { toast } from 'sonner'
import type { HistoryRecord } from '@/types/ipc'

function formatTimestamp(ts: string): string {
  try {
    const d = new Date(ts)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
      ' · ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  } catch {
    return ts
  }
}

interface ActivityListProps {
  items: HistoryRecord[]
  lineClamp?: number
  title?: string
  showViewAll?: boolean
  onViewAll?: () => void
  onDelete?: (id: number) => void
  onToggleFavorite?: (id: number) => void
}

export default function ActivityList({
  items,
  lineClamp = 2,
  title = 'Recent Activity',
  showViewAll = false,
  onViewAll,
  onDelete,
  onToggleFavorite,
}: ActivityListProps) {
  const [copiedId, setCopiedId] = useState<number | null>(null)

  const handleCopy = useCallback(async (item: HistoryRecord) => {
    try {
      await navigator.clipboard.writeText(item.text)
      setCopiedId(item.id)
      toast.success('Copied to clipboard')
      setTimeout(() => setCopiedId(null), 2000)
    } catch {
      toast.error('Failed to copy')
    }
  }, [])

  const handleDelete = useCallback((id: number) => {
    onDelete?.(id)
    toast.success('Item Deleted Successfully')
  }, [onDelete])

  const handleFavorite = useCallback((id: number) => {
    onToggleFavorite?.(id)
  }, [onToggleFavorite])

  if (items.length === 0) return null

  return (
    <div className="w-full mt-4">
      <div className="flex items-center justify-between w-full mb-2.5">
        <span className="text-[12px] font-semibold text-(--text-primary)">
          {title}
        </span>
        {showViewAll && onViewAll && (
          <button
            onClick={onViewAll}
            className="text-[12px] font-semibold text-(--text-muted) hover:text-(--text-primary) bg-transparent border-none p-0 cursor-pointer transition-colors"
          >
            View all
          </button>
        )}
      </div>
      <div className="rounded-lg border border-border bg-(--bg-subtle) divide-y divide-border">
        {items.map((item) => (
          <div
            key={item.id}
            className="flex items-start gap-3 px-3.5 py-2.5"
          >
            <div className="flex-1 min-w-0">
              <p
                className="text-[13px] text-(--text-primary) leading-snug overflow-hidden text-ellipsis"
                style={{
                  display: '-webkit-box',
                  WebkitLineClamp: lineClamp,
                  WebkitBoxOrient: 'vertical',
                }}
              >
                {item.text}
              </p>
              <span className="text-[10px] text-(--text-muted) opacity-60 mt-0.5 block">
                {formatTimestamp(item.timestamp)}
                {item.word_count != null && (
                  <>
                    <span className="mx-1">·</span>
                    {item.word_count} words
                  </>
                )}
              </span>
            </div>
            <button
              onClick={() => handleFavorite(item.id)}
              className="shrink-0 mt-0.5 text-(--text-muted) hover:text-amber-400 transition-colors bg-transparent border-none p-0 cursor-pointer"
              title={item.favorite ? 'Remove from favorites' : 'Add to favorites'}
            >
              <Star className={`h-3.5 w-3.5 ${item.favorite ? 'fill-amber-400 text-amber-400' : ''}`} />
            </button>
            <button
              onClick={() => handleCopy(item)}
              className="shrink-0 mt-0.5 text-(--text-muted) hover:text-(--text-primary) transition-colors bg-transparent border-none p-0 cursor-pointer"
              title="Copy text"
            >
              {copiedId === item.id ? (
                <Check className="h-3.5 w-3.5" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </button>
            <button
              onClick={() => handleDelete(item.id)}
              className="shrink-0 mt-0.5 text-(--text-muted) hover:text-red-400 transition-colors bg-transparent border-none p-0 cursor-pointer"
              title="Delete"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}