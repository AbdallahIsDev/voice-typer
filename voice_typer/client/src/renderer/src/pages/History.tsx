import { useState, useEffect, useCallback, useRef } from 'react'
import { usePython, usePythonEvent } from '@/hooks/usePython'
import { HugeiconsIcon } from '@hugeicons/react'
import { HistoryIcon } from '@hugeicons/core-free-icons'
import { Search, Trash2, Star, Download, ChevronDown } from 'lucide-react'
import { toast } from 'sonner'
import PageHeading from '@/components/PageHeading'
import ActivityList from '@/components/ActivityList'
import type { HistoryRecord, TodayStats, WindowBridge } from '@/types/ipc'

const wb = window.window_ as WindowBridge

// Module-level cache — persists across page navigations so the records list
// and stats render instantly on re-visit instead of showing a spinner.
let _cachedRecords: HistoryRecord[] = []
let _cachedStats: TodayStats | null = null

const PAGE_SIZE = 50

export default function HistoryPage() {
  const { call } = usePython()
  const [records, setRecords] = useState<HistoryRecord[]>(_cachedRecords)
  const [stats, setStats] = useState<TodayStats | null>(_cachedStats)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [hasMore, setHasMore] = useState(true)
  const [searchQuery, setSearchQuery] = useState('')
  const [favoritesOnly, setFavoritesOnly] = useState(false)
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [confirmClear, setConfirmClear] = useState(false)
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const el = document.getElementById('export-format-menu')
    if (!el) return
    const close = (e: MouseEvent) => {
      if (!el.contains(e.target as Node) && !(e.target as HTMLElement)?.closest?.('[data-export-btn]')) {
        el.classList.add('hidden')
      }
    }
    document.addEventListener('click', close)
    return () => document.removeEventListener('click', close)
  }, [])

  const load = useCallback(async (query?: string, favs?: boolean) => {
    setLoading(true)
    try {
      const isFav = favs ?? favoritesOnly
      const q = query ?? searchQuery

      let recs: HistoryRecord[]
      if (q.trim()) {
        recs = await call<HistoryRecord[]>('search_history', { query: q.trim(), limit: PAGE_SIZE, offset: 0 })
      } else if (isFav) {
        recs = await call<HistoryRecord[]>('get_favorites', { limit: PAGE_SIZE, offset: 0 })
      } else {
        recs = await call<HistoryRecord[]>('get_history', { limit: PAGE_SIZE, offset: 0 })
      }
      // Only cache the all-records view — search/filter results are transient
      // and shouldn't pollute the cache that initializes the page on re-visit.
      if (!q.trim() && !isFav) {
        _cachedRecords = recs
      }
      setHasMore(recs.length >= PAGE_SIZE)
      setRecords(recs)

      const todayStats = await call<TodayStats>('get_today_stats')
      _cachedStats = todayStats
      setStats(todayStats)
    } catch (err) {
      console.error('Failed to load history:', err)
    } finally {
      setLoading(false)
    }
  }, [call, searchQuery, favoritesOnly])

  const loadMore = useCallback(async () => {
    setLoadingMore(true)
    try {
      const isFav = favoritesOnly
      const q = searchQuery
      const offset = records.length

      let newRecs: HistoryRecord[]
      if (q.trim()) {
        newRecs = await call<HistoryRecord[]>('search_history', { query: q.trim(), limit: PAGE_SIZE, offset })
      } else if (isFav) {
        newRecs = await call<HistoryRecord[]>('get_favorites', { limit: PAGE_SIZE, offset })
      } else {
        newRecs = await call<HistoryRecord[]>('get_history', { limit: PAGE_SIZE, offset })
      }
      setHasMore(newRecs.length >= PAGE_SIZE)
      if (newRecs.length > 0) {
        setRecords(prev => [...prev, ...newRecs])
      }
    } catch (err) {
      console.error('Failed to load more history:', err)
    } finally {
      setLoadingMore(false)
    }
  }, [call, searchQuery, favoritesOnly, records.length])

  // ── Proactive background refresh after new transcriptions ────────
  //
  // When a transcription_final event arrives (from any page), refresh the
  // cached stats and records so the next visit to History shows fresh data
  // instead of stale cache.  If the user is *already* on the History page
  // and not mid-search, also update the visible UI.
  usePythonEvent('transcription_final', useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current)
    refreshTimer.current = setTimeout(async () => {
      try {
        const [newStats, newRecs] = await Promise.all([
          call<TodayStats>('get_today_stats'),
          call<HistoryRecord[]>('get_history', { limit: PAGE_SIZE, offset: 0 }),
        ])
        _cachedStats = newStats
        _cachedRecords = newRecs
        setStats(newStats)
        // Only replace visible records when no search/filter is active
        if (!searchQuery && !favoritesOnly) {
          setHasMore(newRecs.length >= PAGE_SIZE)
          setRecords(newRecs)
        }
      } catch {
        // Silently ignore — the next manual load will pick up fresh data
      }
    }, 500)
  }, [call, searchQuery, favoritesOnly]))

  // Clean up pending refresh timer on unmount
  useEffect(() => {
    return () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current)
    }
  }, [])

  useEffect(() => { load() }, [])

  const handleSearch = useCallback((value: string) => {
    setSearchQuery(value)
    if (searchTimer.current) clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => {
      load(value, favoritesOnly)
    }, 200)
  }, [load, favoritesOnly])

  const toggleFavorites = useCallback(() => {
    const next = !favoritesOnly
    setFavoritesOnly(next)
    load(searchQuery, next)
  }, [favoritesOnly, load, searchQuery])

  const handleDelete = useCallback(async (id: number) => {
    try {
      await call('delete_history', { id })
      setRecords(prev => prev.filter(r => r.id !== id))
    } catch {
      toast.error('Failed to delete item')
    }
  }, [call])

  const handleToggleFavorite = useCallback(async (id: number) => {
    try {
      const res = await call<{ favorite: number }>('toggle_favorite', { id })
      setRecords(prev => prev.map(r => r.id === id ? { ...r, favorite: res.favorite } : r))
    } catch {
      toast.error('Failed to toggle favorite')
    }
  }, [call])

  const handleClearAll = useCallback(async () => {
    // Nothing to clear — don't call backend, don't show toast
    if (records.length === 0) return

    if (!confirmClear) {
      setConfirmClear(true)
      setTimeout(() => setConfirmClear(false), 3000)
      return
    }
    try {
      await call('clear_history')
      const emptyStats = { count: 0, chars: 0, word_count: 0, duration: 0 }
      _cachedStats = emptyStats
      _cachedRecords = []
      setRecords([])
      setStats(emptyStats)
      setHasMore(false)
      setConfirmClear(false)
      toast.success('History cleared')
    } catch {
      toast.error('Failed to clear history')
    }
  }, [call, confirmClear, records.length])

  const handleExport = useCallback(async () => {
    const el = document.getElementById('export-format-menu')
    if (el) {
      el.classList.toggle('hidden')
    }
  }, [])

  const doExport = useCallback(async (format: 'json' | 'csv') => {
    const el = document.getElementById('export-format-menu')
    el?.classList.add('hidden')
    if (records.length === 0) {
      toast.error('Nothing to export — history is empty')
      return
    }
    try {
      const all = await call<HistoryRecord[]>('get_history', { limit: 10000 })
      const result = await (window.window_ as WindowBridge).exportHistory(all, format)
      if (result.success) {
        const filename = result.path!.split(/[\\/]/).pop()!
        toast.success(`${filename} saved successfully`)
      }
    } catch {
      toast.error('Export failed')
    }
  }, [call, records.length])

  return (
    <div className="animate-fade-in-up mx-auto flex min-h-full w-full max-w-2xl flex-col px-6 py-6">
      <PageHeading
        title="History"
        description={stats ? `${stats.count} transcription${stats.count !== 1 ? 's' : ''} today${stats.chars > 0 ? ` (${stats.chars.toLocaleString()} chars)` : ''}` : '0 transcriptions today'}
      />

      {/* Search */}
      <div className="relative mt-4">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-(--text-muted) pointer-events-none" />
        <input
          type="text"
          value={searchQuery}
          onChange={e => handleSearch(e.target.value)}
          placeholder="Search history..."
          className="w-full h-9 rounded-xl border border-border bg-(--bg-subtle) pl-9 pr-3 text-sm text-(--text-primary) outline-none placeholder:text-(--text-muted) focus:border-ring transition-colors"
        />
      </div>

      {/* Action buttons */}
      <div className="flex items-center gap-2 mt-3">
        <button
          onClick={toggleFavorites}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium transition-colors border border-border ${
            favoritesOnly
              ? 'bg-amber-400/15 text-amber-400 border-amber-400/30'
              : 'bg-(--bg-subtle) text-(--text-muted) hover:text-(--text-primary)'
          }`}
        >
          <Star className={`h-3.5 w-3.5 ${favoritesOnly ? 'fill-amber-400' : ''}`} />
          Favorites
        </button>
        <button
          onClick={handleClearAll}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium transition-colors border border-border ${
            confirmClear
              ? 'bg-red-500/15 text-red-400 border-red-500/30 animate-pulse'
              : 'bg-(--bg-subtle) text-(--text-muted) hover:text-red-400'
          }`}
        >
          <Trash2 className="h-3.5 w-3.5" />
          {confirmClear ? 'Click again to confirm' : 'Clear All'}
        </button>
        <div className="relative ml-auto">
          <button
            data-export-btn
            onClick={handleExport}
            disabled={records.length === 0}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium bg-(--bg-subtle) text-(--text-muted) hover:text-(--text-primary) transition-colors border border-border disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:text-(--text-muted)"
          >
            <Download className="h-3.5 w-3.5" />
            Export
          </button>
          <div
            id="export-format-menu"
            className="hidden absolute right-0 top-full mt-1 z-10 w-30 rounded-xl border border-border bg-(--bg-subtle) shadow-lg overflow-hidden"
          >
            <button
              onClick={() => doExport('json')}
              className="w-full px-3 py-2 text-xs text-left text-(--text-primary) hover:bg-(--surface-hover) transition-colors"
            >
              Export as JSON
            </button>
            <button
              onClick={() => doExport('csv')}
              className="w-full px-3 py-2 text-xs text-left text-(--text-primary) hover:bg-(--surface-hover) transition-colors"
            >
              Export as CSV
            </button>
          </div>
        </div>
      </div>

      {loading && records.length === 0 ? (
        <div className="flex min-h-full items-center justify-center py-20">
          <div className="h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
      ) : records.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 gap-3">
          <HugeiconsIcon icon={HistoryIcon} className="h-8 w-8 text-(--text-muted) opacity-30" />
          <p className="text-sm text-(--text-muted) opacity-50">
            {searchQuery ? 'No results found' : favoritesOnly ? 'No favorites yet' : 'No transcriptions yet'}
          </p>
        </div>
      ) : (
        <>
          <ActivityList
            items={records}
            lineClamp={3}
            onDelete={handleDelete}
            onToggleFavorite={handleToggleFavorite}
          />

          {hasMore && (
            <button
              onClick={loadMore}
              disabled={loadingMore}
              className="mt-4 w-full flex items-center justify-center gap-2 py-3 rounded-xl text-xs font-medium text-(--text-muted) hover:text-(--text-secondary) hover:bg-(--surface-hover) transition-colors border border-dashed border-border/30 disabled:opacity-50"
            >
              {loadingMore ? (
                <>
                  <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
                  Loading...
                </>
              ) : (
                <>
                  <ChevronDown className="h-3.5 w-3.5" />
                  Load More
                </>
              )}
            </button>
          )}
        </>
      )}
    </div>
  )
}