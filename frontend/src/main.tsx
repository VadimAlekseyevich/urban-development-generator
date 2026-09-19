import ReactDOM from 'react-dom/client'
import 'maplibre-gl/dist/maplibre-gl.css'
import './styles.css'
import './diagnostics.css'
import './zoning.css'
import './roads.css'
import './blockParcels.css'
import './buildings.css'
import './demography.css'
import App from './App'
import { RuntimeDiagnostics } from './RuntimeDiagnostics'
import { installGlobalDiagnostics } from './diagnostics'

installGlobalDiagnostics()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <RuntimeDiagnostics>
    <App />
  </RuntimeDiagnostics>,
)
