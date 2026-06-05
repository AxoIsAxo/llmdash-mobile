import { useState, useRef, useEffect, useCallback } from 'react'
import { Mic, MicOff, Loader2, AlertCircle } from 'lucide-react'
import { VoiceRecorder, RecordingData } from 'capacitor-voice-recorder'
import { api } from '../api'

interface VoiceButtonProps {
  onTranscribed: (text: string) => void
  disabled?: boolean
}

type ButtonState = 'idle' | 'recording' | 'transcribing' | 'error'

function base64ToBlob(base64: string, mimeType: string): Blob {
  const byteChars = atob(base64)
  const parts: ArrayBuffer[] = []
  const sliceSize = 1024
  for (let offset = 0; offset < byteChars.length; offset += sliceSize) {
    const slice = byteChars.slice(offset, offset + sliceSize)
    const buf = new ArrayBuffer(slice.length)
    const view = new Uint8Array(buf)
    for (let i = 0; i < slice.length; i++) {
      view[i] = slice.charCodeAt(i)
    }
    parts.push(buf)
  }
  return new Blob(parts, { type: mimeType })
}

function extensionForMime(mimeType: string): string {
  if (mimeType.includes('webm')) return 'webm'
  if (mimeType.includes('mp4') || mimeType.includes('aac')) return 'm4a'
  if (mimeType.includes('ogg')) return 'ogg'
  if (mimeType.includes('wav')) return 'wav'
  if (mimeType.includes('amr')) return 'amr'
  return 'audio'
}

export default function VoiceButton({
  onTranscribed,
  disabled = false,
}: VoiceButtonProps) {
  const [buttonState, setButtonState] = useState<ButtonState>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const recordingRef = useRef(false)

  const ensurePermission = useCallback(async (): Promise<boolean> => {
    try {
      const canRecord = await VoiceRecorder.canDeviceVoiceRecord()
      if (!canRecord.value) {
        setErrorMsg('This device cannot record audio')
        setButtonState('error')
        return false
      }

      const status = await VoiceRecorder.hasAudioRecordingPermission()
      if (status.value) return true

      const requested = await VoiceRecorder.requestAudioRecordingPermission()
      if (!requested.value) {
        setErrorMsg('Microphone access denied. Allow microphone access in app settings.')
        setButtonState('error')
        return false
      }
      return true
    } catch (e: any) {
      setErrorMsg(e?.message || 'Failed to check microphone permission')
      setButtonState('error')
      return false
    }
  }, [])

  const stopAndTranscribe = useCallback(async () => {
    setButtonState('transcribing')
    try {
      const result: RecordingData = await VoiceRecorder.stopRecording()
      recordingRef.current = false

      const base64 = result.value.recordDataBase64
      const mimeType = result.value.mimeType || 'audio/aac'
      if (!base64) {
        setButtonState('idle')
        return
      }

      const blob = base64ToBlob(base64, mimeType)
      const filename = `recording.${extensionForMime(mimeType)}`
      const data = await api.chat.transcribe(blob, filename)
      const text = data.text || ''
      if (text.trim()) {
        onTranscribed(text.trim())
      }
      setButtonState('idle')
    } catch (e: any) {
      recordingRef.current = false
      const message = e?.message || String(e)
      if (message.includes('EMPTY_RECORDING') || message.includes('RECORDING_HAS_NOT_STARTED')) {
        setButtonState('idle')
        return
      }
      console.error('[VoiceButton] transcription failed:', e)
      setErrorMsg(message || 'Transcription failed')
      setButtonState('error')
    }
  }, [onTranscribed])

  const handleClick = useCallback(async () => {
    if (disabled) return
    if (buttonState === 'transcribing') return

    if (buttonState === 'recording') {
      await stopAndTranscribe()
      return
    }

    setErrorMsg(null)

    if (buttonState === 'error') {
      setButtonState('idle')
    }

    const ok = await ensurePermission()
    if (!ok) return

    try {
      await VoiceRecorder.startRecording()
      recordingRef.current = true
      setButtonState('recording')
    } catch (e: any) {
      recordingRef.current = false
      const message = e?.message || String(e)
      if (message.includes('ALREADY_RECORDING')) {
        try {
          await VoiceRecorder.stopRecording()
        } catch {}
        recordingRef.current = false
        setButtonState('idle')
        return
      }
      console.error('[VoiceButton] start failed:', e)
      setErrorMsg(message || 'Failed to start recording')
      setButtonState('error')
    }
  }, [buttonState, disabled, ensurePermission, stopAndTranscribe])

  useEffect(() => {
    return () => {
      if (recordingRef.current) {
        VoiceRecorder.stopRecording().catch(() => {})
        recordingRef.current = false
      }
    }
  }, [])

  const showError = buttonState === 'error'

  return (
    <div className="relative">
      <button
        onClick={handleClick}
        disabled={disabled || buttonState === 'transcribing'}
        title={
          buttonState === 'recording'
            ? 'Recording... click to stop'
            : buttonState === 'transcribing'
            ? 'Transcribing...'
            : showError
            ? errorMsg || 'Error'
            : 'Click to start recording'
        }
        className={`p-3 rounded-xl transition-colors relative ${
          buttonState === 'recording'
            ? 'bg-theme-danger hover:bg-theme-danger-hover animate-pulse'
            : showError
            ? 'bg-theme-amber hover:bg-theme-amber'
            : buttonState === 'transcribing'
            ? 'bg-theme-purple'
            : 'bg-theme-bg-elevated hover:bg-theme-bg-active'
        } disabled:opacity-50 disabled:cursor-not-allowed`}
      >
        {buttonState === 'transcribing' ? (
          <Loader2 className="w-5 h-5 animate-spin text-theme-purple" />
        ) : buttonState === 'recording' ? (
          <MicOff className="w-5 h-5 text-white" />
        ) : showError ? (
          <AlertCircle className="w-5 h-5 text-white" />
        ) : (
          <Mic className="w-5 h-5 text-theme-subtle" />
        )}
      </button>
      {showError && errorMsg && (
        <div
          role="alert"
          className="absolute bottom-full right-0 mb-2 max-w-[260px] bg-theme-amber/95 text-theme-bg px-3 py-2 rounded-lg text-xs shadow-lg z-50"
        >
          {errorMsg}
        </div>
      )}
    </div>
  )
}
