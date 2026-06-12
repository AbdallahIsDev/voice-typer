// src/renderer/src/pages/Vocabulary.tsx

import { useState, useEffect, useCallback } from 'react'
import { HugeiconsIcon } from '@hugeicons/react'
import {
  BookOpen02Icon,
  Add01Icon,
  Edit01Icon,
  Delete01Icon,
} from '@hugeicons/core-free-icons'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

// Vocabulary is stored client-side in localStorage because the Python Config
// dataclass has no `vocabulary_data` field and the IPC server drops writes
// for unknown keys. See docs/agents/gap-fix-prompt.md and the user's brief.
const STORAGE_KEY = 'vocabulary_data'

const CATEGORIES = [
  'misspellings',
  'phrase_corrections',
  'extra_word_patterns',
  'technical_terms',
  'names',
  'products',
] as const

const CATEGORY_LABELS: Record<string, string> = {
  misspellings: 'Misspellings',
  phrase_corrections: 'Phrase Corrections',
  extra_word_patterns: 'Extra Word Patterns',
  technical_terms: 'Technical Terms',
  names: 'Names',
  products: 'Products',
}

const CATEGORY_DESCRIPTIONS: Record<string, string> = {
  misspellings: 'Common word misspellings → corrections',
  phrase_corrections: 'Phrase-level corrections',
  extra_word_patterns: 'Patterns to remove or replace',
  technical_terms: 'Technical jargon corrections',
  names: 'Proper name corrections',
  products: 'Product name corrections',
}

interface VocabularyData {
  misspellings?: Record<string, string>
  technical_terms?: Record<string, string>
  names?: Record<string, string>
  products?: Record<string, string>
  phrase_corrections?: Array<[string, string]>
  extra_word_patterns?: Array<[string, string]>
}

interface VocabularyEntry {
  category: string
  original: string
  correction: string
  index?: number
}

function loadVocabularyData(): VocabularyData {
  try {
    const raw = localStorage.getItem(STORAGE_KEY) ?? '{}'
    const parsed = JSON.parse(raw)
    return typeof parsed === 'object' && parsed !== null ? parsed : {}
  } catch {
    return {}
  }
}

function saveVocabularyData(data: VocabularyData): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(data))
}

function flattenEntries(data: VocabularyData): VocabularyEntry[] {
  const items: VocabularyEntry[] = []
  for (const cat of CATEGORIES) {
    const catData = (data as Record<string, unknown>)[cat]
    if (
      cat === 'misspellings' ||
      cat === 'technical_terms' ||
      cat === 'names' ||
      cat === 'products'
    ) {
      if (typeof catData === 'object' && catData !== null) {
        for (const [key, val] of Object.entries(catData as Record<string, string>)) {
          items.push({ category: cat, original: key, correction: String(val) })
        }
      }
    } else if (cat === 'phrase_corrections' || cat === 'extra_word_patterns') {
      if (Array.isArray(catData)) {
        catData.forEach((entry: unknown, i: number) => {
          if (Array.isArray(entry) && entry.length >= 2) {
            items.push({
              category: cat,
              original: entry[0] as string,
              correction: entry[1] as string,
              index: i,
            })
          }
        })
      }
    }
  }
  return items
}

export default function VocabularyPage() {
  const [entries, setEntries] = useState<VocabularyEntry[]>([])
  const [activeCategory, setActiveCategory] = useState<string>(CATEGORIES[0])
  const [loading, setLoading] = useState(true)
  const [showDialog, setShowDialog] = useState(false)
  const [editingEntry, setEditingEntry] = useState<VocabularyEntry | null>(null)
  const [original, setOriginal] = useState('')
  const [correction, setCorrection] = useState('')
  const [dialogCategory, setDialogCategory] = useState(activeCategory)
  const [snackbar, setSnackbar] = useState<{ message: string; type: 'success' | 'error' | 'warning' } | null>(null)

  const showSnack = (message: string, type: 'success' | 'error' | 'warning') => {
    setSnackbar({ message, type })
    setTimeout(() => setSnackbar(null), 3000)
  }

  const loadVocabulary = useCallback(() => {
    const data = loadVocabularyData()
    setEntries(flattenEntries(data))
    setLoading(false)
  }, [])

  useEffect(() => {
    loadVocabulary()
  }, [loadVocabulary])

  const filteredEntries = entries.filter((e) => e.category === activeCategory)

  const openAddDialog = () => {
    setEditingEntry(null)
    setOriginal('')
    setCorrection('')
    setDialogCategory(activeCategory)
    setShowDialog(true)
  }

  const openEditDialog = (entry: VocabularyEntry) => {
    setEditingEntry(entry)
    setOriginal(entry.original)
    setCorrection(entry.correction)
    setDialogCategory(entry.category)
    setShowDialog(true)
  }

  const saveEntry = () => {
    if (!original.trim() || !correction.trim()) {
      showSnack('Please fill in both fields', 'warning')
      return
    }
    try {
      const data = loadVocabularyData()
      const cat = dialogCategory
      const origTrim = original.trim()
      const corrTrim = correction.trim()

      if (
        cat === 'misspellings' ||
        cat === 'technical_terms' ||
        cat === 'names' ||
        cat === 'products'
      ) {
        const bucket = (data[cat as 'misspellings'] ??= {})
        if (editingEntry && editingEntry.category === cat) {
          delete bucket[editingEntry.original]
        }
        bucket[origTrim] = corrTrim
      } else {
        const arr = (data[cat as 'phrase_corrections'] ??= [])
        if (editingEntry && editingEntry.index !== undefined && editingEntry.category === cat) {
          arr[editingEntry.index] = [origTrim, corrTrim]
        } else {
          arr.push([origTrim, corrTrim])
        }
      }

      saveVocabularyData(data)
      showSnack(
        editingEntry
          ? `Updated: ${origTrim} → ${corrTrim}`
          : `Added: ${origTrim} → ${corrTrim}`,
        'success',
      )
      setShowDialog(false)
      loadVocabulary()
    } catch (err) {
      console.error('Failed to save entry', err)
      showSnack('Failed to save entry', 'error')
    }
  }

  const deleteEntry = (entry: VocabularyEntry) => {
    try {
      const data = loadVocabularyData()
      const cat = entry.category

      if (
        cat === 'misspellings' ||
        cat === 'technical_terms' ||
        cat === 'names' ||
        cat === 'products'
      ) {
        const bucket = data[cat as 'misspellings']
        if (bucket && entry.original in bucket) {
          delete bucket[entry.original]
        }
      } else {
        const arr = data[cat as 'phrase_corrections']
        if (arr && entry.index !== undefined) {
          arr.splice(entry.index, 1)
        }
      }

      saveVocabularyData(data)
      showSnack(`Deleted: ${entry.original}`, 'warning')
      loadVocabulary()
    } catch (err) {
      console.error('Failed to delete entry', err)
      showSnack('Failed to delete entry', 'error')
    }
  }

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
      </div>
    )
  }

  return (
    <div className="animate-fade-in-up mx-auto flex h-full w-full max-w-2xl flex-col overflow-hidden">
      <div className="space-y-1 px-6 pb-4 pt-6">
        <div className="flex items-center justify-between">
          <h1 className="font-sans text-2xl font-bold tracking-tight text-[var(--text-primary)]">
            Custom Vocabulary
          </h1>
          <Button variant="default" className="gap-2" onClick={openAddDialog}>
            <HugeiconsIcon icon={Add01Icon} className="h-4 w-4" />
            Add Word
          </Button>
        </div>
        <p className="text-sm text-[var(--text-muted)]">
          Add custom words and corrections to improve accuracy
        </p>
      </div>

      {/* Category Tabs */}
      <div className="px-4">
        <div className="flex gap-0.5 rounded-lg bg-[var(--bg-subtle)] p-0.5">
          {CATEGORIES.map((cat) => (
            <button
              key={cat}
              onClick={() => setActiveCategory(cat)}
              className={cn(
                'flex-1 rounded-md px-3 py-1.5 text-xs font-medium transition-all',
                activeCategory === cat
                  ? 'bg-[var(--bg)] text-[var(--text-primary)] shadow-sm'
                  : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]',
              )}
            >
              {CATEGORY_LABELS[cat]}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4 flex-1 overflow-y-auto px-4 pb-4">
        {filteredEntries.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-4 py-16">
            <HugeiconsIcon icon={BookOpen02Icon} className="h-10 w-10 text-[var(--text-muted)] opacity-30" />
            <p className="text-sm text-[var(--text-muted)]">
              No {CATEGORY_LABELS[activeCategory].toLowerCase()}
            </p>
            <p className="text-xs text-[var(--text-muted)] opacity-70">
              {CATEGORY_DESCRIPTIONS[activeCategory]}
            </p>
            <Button variant="default" className="mt-2 gap-2" onClick={openAddDialog}>
              <HugeiconsIcon icon={Add01Icon} className="h-4 w-4" />
              Add First Word
            </Button>
          </div>
        ) : (
          <div className="space-y-1">
            {filteredEntries.map((entry, idx) => (
              <div
                key={`${entry.original}-${idx}`}
                className={cn(
                  'flex items-center justify-between rounded-lg px-5 py-3.5',
                  'border border-[var(--border)] bg-[var(--bg-subtle)]',
                  'transition-colors hover:bg-[var(--surface-hover)]',
                )}
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2.5">
                    <span className="text-sm font-medium text-destructive">
                      {entry.original}
                    </span>
                    <span className="text-sm text-[var(--text-muted)]">→</span>
                    <span className="text-sm font-semibold text-primary">
                      {entry.correction}
                    </span>
                  </div>
                  <p className="mt-0.5 text-xs text-[var(--text-muted)]">
                    {CATEGORY_LABELS[entry.category]}
                  </p>
                </div>
                <div className="ml-3 flex shrink-0 items-center gap-1">
                  <button
                    onClick={() => openEditDialog(entry)}
                    className="rounded p-1.5 text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-hover)] hover:text-[var(--accent)]"
                    title="Edit"
                  >
                    <HugeiconsIcon icon={Edit01Icon} className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => deleteEntry(entry)}
                    className="rounded p-1.5 text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-hover)] hover:text-destructive"
                    title="Delete"
                  >
                    <HugeiconsIcon icon={Delete01Icon} className="h-4 w-4" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Add/Edit Dialog */}
      {showDialog && (
        <div className="animate-fade-in fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div
            className={cn(
              'animate-scale-in w-[420px] rounded-xl border border-[var(--border)]',
              'bg-[var(--bg)] p-6 shadow-2xl',
            )}
          >
            <h2 className="mb-5 text-lg font-semibold text-[var(--text-primary)]">
              {editingEntry ? 'Edit Vocabulary Entry' : 'Add Vocabulary Entry'}
            </h2>

            <div className="space-y-4">
              <div>
                <label className="mb-1.5 block text-sm font-medium text-[var(--text-primary)]">
                  Original (misrecognized word)
                </label>
                <Input
                  value={original}
                  onChange={(e) => setOriginal(e.target.value)}
                  placeholder="e.g., their"
                  className="w-full"
                />
              </div>

              <div>
                <label className="mb-1.5 block text-sm font-medium text-[var(--text-primary)]">
                  Correction
                </label>
                <Input
                  value={correction}
                  onChange={(e) => setCorrection(e.target.value)}
                  placeholder="e.g., there"
                  className="w-full"
                />
              </div>

              {!editingEntry && (
                <div>
                  <label className="mb-1.5 block text-sm font-medium text-[var(--text-primary)]">
                    Category
                  </label>
                  <Select value={dialogCategory} onValueChange={setDialogCategory}>
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CATEGORIES.map((cat) => (
                        <SelectItem key={cat} value={cat}>
                          {CATEGORY_LABELS[cat]}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <Button variant="ghost" onClick={() => setShowDialog(false)}>
                Cancel
              </Button>
              <Button variant="default" onClick={saveEntry}>
                Save
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Snackbar */}
      {snackbar && (
        <div
          className={cn(
            'animate-slide-up fixed bottom-6 left-1/2 z-50 -translate-x-1/2',
            'rounded-lg px-4 py-2.5 text-sm shadow-lg',
            snackbar.type === 'success' && 'bg-primary text-primary-foreground',
            snackbar.type === 'error' && 'bg-destructive text-white',
            snackbar.type === 'warning' && 'bg-primary text-primary-foreground',
          )}
        >
          {snackbar.message}
        </div>
      )}
    </div>
  )
}
