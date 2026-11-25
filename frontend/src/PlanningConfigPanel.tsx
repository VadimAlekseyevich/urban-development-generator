import { useState } from 'react'

type Props = {
  apiBase: string
  projectId: string | null
  disabled: boolean
  onLoading: (loading: boolean) => void
  onApply: (config: string) => void
}

const FIELDS = [
  { key: 'cell_size_m', label: 'Размер ячейки, м', initial: 10, min: 2, max: 100, step: 1 },
  { key: 'residential_percent', label: 'Жилая зона, %', initial: 60, min: 1, max: 100, step: 1 },
  { key: 'mixed_percent', label: 'Смешанная зона, %', initial: 20, min: 0, max: 99, step: 1 },
  { key: 'public_percent', label: 'Общественная зона, %', initial: 10, min: 0, max: 99, step: 1 },
  { key: 'recreation_percent', label: 'Рекреационная зона, %', initial: 10, min: 0, max: 99, step: 1 },
  { key: 'zoning_seeds', label: 'Опорные точки зонирования', initial: 16, min: 1, max: 128, step: 1 },
  { key: 'road_anchors', label: 'Лимит дорожных опорных точек', initial: 24, min: 3, max: 64, step: 1 },
  { key: 'max_block_area_m2', label: 'Предельная площадь квартала, м²', initial: 20000, min: 400, max: 100000, step: 100 },
  { key: 'floors', label: 'Этажность', initial: 4, min: 1, max: 16, step: 1 },
  { key: 'coverage_ratio', label: 'Доля пятна застройки (0–1)', initial: 0.25, min: 0.05, max: 0.6, step: 0.01 },
  { key: 'target_population', label: 'Целевое население, чел.', initial: 1000, min: 1, max: 1000000, step: 1 },
  { key: 'area_per_person_m2', label: 'Жилая площадь на человека, м²', initial: 30, min: 10, max: 100, step: 1 },
  { key: 'clinic_distance_m', label: 'Доступность клиники по сети, м', initial: 800, min: 100, max: 3000, step: 10 },
  { key: 'clinic_capacity', label: 'Вместимость клиники, чел.', initial: 1000, min: 50, max: 10000, step: 1 },
] as const

export function PlanningConfigPanel({ apiBase, projectId, disabled, onLoading, onApply }: Props) {
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(FIELDS.map((field) => [field.key, String(field.initial)])),
  )
  const [source, setSource] = useState('slope')
  const [error, setError] = useState<string | null>(null)
  const [applied, setApplied] = useState(false)
  const total = ['residential_percent', 'mixed_percent', 'public_percent', 'recreation_percent']
    .reduce((sum, key) => sum + Number(values[key]), 0)
  const far = Number(values.floors) * Number(values.coverage_ratio)

  async function apply(): Promise<void> {
    if (!projectId || disabled) return
    setError(null)
    setApplied(false)
    const payload: Record<string, number | string> = { suitability_source: source }
    for (const field of FIELDS) {
      const raw = values[field.key]
      const number = Number(raw)
      if (!raw.trim() || !Number.isFinite(number) || number < field.min ||
          number > field.max || (field.step === 1 && !Number.isInteger(number))) {
        setError('Проверьте поле: ' + field.label)
        return
      }
      payload[field.key] = number
    }
    if (total !== 100) {
      setError('Сумма долей зон должна равняться 100%.')
      return
    }
    onLoading(true)
    try {
      const response = await fetch(apiBase.replace(/\/$/, '') + '/generation/planning-config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        throw new Error('HTTP ' + response.status + ': ' + (await response.text()).slice(0, 400))
      }
      const config: unknown = await response.json()
      if (!config || typeof config !== 'object' || !('schema_version' in config) ||
          config.schema_version !== 'canonical-stage-configs-v5') {
        throw new Error('Сервер вернул неизвестную конфигурацию.')
      }
      onApply(JSON.stringify(config, null, 2))
      setApplied(true)
    } catch (caught: unknown) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      onLoading(false)
    }
  }

  return (
    <fieldset className="run-inputs planning-config" disabled={!projectId || disabled}>
      <legend>Стартовый профиль района</legend>
      <p className="helper-text">
        Модельные допущения, не градостроительные нормы. Один тип прямоугольного здания,
        рекреация без зданий, один тип клиники. Результат зависит от входов и валидации.
        Изменения полей применяются к JSON только кнопкой формирования профиля.
      </p>
      <label>Источник пригодности территории
        <select value={source} onChange={(event) => { setSource(event.target.value); setApplied(false) }}>
          <option value="slope">Уклон (нужен готовый DEM)</option>
          <option value="landuse">Землепользование (нужен готовый landuse)</option>
        </select>
      </label>
      <p className="helper-text">
        {source === 'slope' ? 'Уклон оценивается от 0 до 30 градусов.'
          : 'Распознаются residential, grass, meadow, farmland; неизвестные классы не оцениваются.'}
      </p>
      {FIELDS.map((field) => <label key={field.key}>{field.label}
        <input type="number" min={field.min} max={field.max} step={field.step}
          value={values[field.key]} onChange={(event) => {
            setValues((current) => ({ ...current, [field.key]: event.target.value }))
            setApplied(false)
          }} />
      </label>)}
      <p className="helper-text">Сумма зон: {total}% · целевой FAR: {Number.isFinite(far) ? far.toFixed(2) : '—'}</p>
      <button type="button" className="button" onClick={() => { void apply() }}>
        Сформировать конфигурацию
      </button>
      {error && <p role="alert" className="form-error">{error}</p>}
      {applied && <p role="status" className="helper-text">
        Профиль сформирован. В редакторе ниже можно изменить его перед запуском.
      </p>}
    </fieldset>
  )
}
