import { useEffect, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1'

function App() {
  const mapContainer = useRef<HTMLDivElement | null>(null)
  const [apiStatus, setApiStatus] = useState<'checking' | 'online' | 'offline'>('checking')

  useEffect(() => {
    fetch(`${API_BASE}/health/live`)
      .then((response) => {
        if (!response.ok) throw new Error('API unavailable')
        setApiStatus('online')
      })
      .catch(() => setApiStatus('offline'))
  }, [])

  useEffect(() => {
    if (!mapContainer.current) return
    const map = new maplibregl.Map({
      container: mapContainer.current,
      style: 'https://demotiles.maplibre.org/style.json',
      center: [37.6176, 55.7558],
      zoom: 9,
    })
    map.addControl(new maplibregl.NavigationControl(), 'top-right')
    return () => map.remove()
  }, [])

  return (
    <main className="layout">
      <aside className="sidebar">
        <h1>Urban Development Generator</h1>
        <p className="muted">Процедурное развитие городской структуры</p>
        <div className={`status status-${apiStatus}`}>API: {apiStatus}</div>
        <section>
          <h2>Проект</h2>
          <p>Здесь появятся создание проекта, загрузка слоёв и параметры генерации.</p>
        </section>
        <section>
          <h2>Конвейер</h2>
          <ol>
            <li>Пригодность территории</li>
            <li>Зонирование</li>
            <li>Дорожная сеть</li>
            <li>Кварталы</li>
            <li>Застройка</li>
            <li>Население и инфраструктура</li>
            <li>Проверки и метрики</li>
          </ol>
        </section>
      </aside>
      <div ref={mapContainer} className="map" />
    </main>
  )
}

export default App
