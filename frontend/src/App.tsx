import { Routes, Route } from 'react-router-dom'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import VerifyPage from './pages/VerifyPage'
import UploadPage from './pages/UploadPage'
import SettingsPage from './pages/SettingsPage'

function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/verify" element={<VerifyPage />} />
        <Route path="/upload" element={<UploadPage />} />
        <Route path="/catalogue" element={<div className="text-gray-400">Catalogue - Coming Soon</div>} />
        <Route path="/settings" element={<SettingsPage />} />
      </Routes>
    </Layout>
  )
}

export default App
