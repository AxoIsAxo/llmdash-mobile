import React, { useState, useRef, useEffect, useCallback } from 'react'
import { Mic, MicOff, Loader2, AlertCircle } from 'lucide-react'

interface VoiceButtonProps {
  onTranscribed: (text: string) => void
  onAudioCaptured?: (audioBlob: Blob, mimeType: string) => void
  audioEnabled?: boolean
  disabled?: boolean
}

type ButtonState = 'idle' | 'recording' | 'processing' | 'error'

function getAuthToken(): string | null {
  return localStorage.getItem('llmdash_token')
}

async function sendAudioForTranscription(audioBlob: Blob): Promise<string> {
  const formData = new FormData()
  formData.append('file', audioBlob, 'recording.webm')

  const headers: Record<string, string> = {}
  const token = getAuthToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch('/api/chat/transcribe', {
    method: 'POST',
    headers,
    body: formData,
  })

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }

  const data = await res.json()
  return data.text || ''
}

export default function VoiceButton({
  onTranscribed,
  onAudioCaptured,
  audioEnabled = false,
  disabled = false,
}: VoiceButtonProps) {
  const [buttonState, setButtonState] = useState<ButtonState>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const chunksRef = useRef<Blob[]>([])

  const cleanupStream = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
    mediaRecorderRef.current = null
  }, [])

  const stopRecording = useCallback(() => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      mediaRecorderRef.current.stop()
    }
    cleanupStream()
  }, [cleanupStream])

  const handleClick = useCallback(async () => {
    if (disabled || buttonState === 'processing') return

    if (buttonState === 'recording') {
      stopRecording()
      return
    }

    setErrorMsg(null)

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      chunksRef.current = []

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : ''

      const mediaRecorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream)
      mediaRecorderRef.current = mediaRecorder
      const usedMime = mediaRecorder.mimeType || 'audio/webm'

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      mediaRecorder.onstop = async () => {
        cleanupStream()

        if (chunksRef.current.length === 0) {
          setButtonState('idle')
          return
        }

        setButtonState('processing')

        try {
          const audioBlob = new Blob(chunksRef.current, { type: usedMime })

          if (audioEnabled && onAudioCaptured) {
            onAudioCaptured(audioBlob, usedMime)
            setButtonState('idle')
            return
          }

          const text = await sendAudioForTranscription(audioBlob)
          if (text.trim()) {
            onTranscribed(text.trim())
          }
          setButtonState('idle')
        } catch (e: any) {
          setErrorMsg(e.message || 'Transcription failed')
          setButtonState('error')
        }
      }

      mediaRecorder.onerror = () => {
        cleanupStream()
        setErrorMsg('Recording error')
        setButtonState('error')
      }

      mediaRecorder.start()
      setButtonState('recording')
    } catch (e: any) {
      if (e.name === 'NotAllowedError') {
        setErrorMsg('Microphone access denied. Allow microphone access in browser settings.')
      } else {
        setErrorMsg(e.message || 'Failed to start recording')
      }
      setButtonState('error')
      cleanupStream()
    }
  }, [buttonState, disabled, onTranscribed, onAudioCaptured, audioEnabled, cleanupStream, stopRecording])

  useEffect(() => {
    return () => {
      cleanupStream()
    }
  }, [cleanupStream])

  const showError = buttonState === 'error'
  const isProcessing = buttonState === 'processing'

  const titleText = isProcessing
    ? audioEnabled
      ? 'Sending audio...'
      : 'Transcribing...'
    : buttonState === 'recording'
    ? 'Recording... click to stop'
    : showError
    ? errorMsg || 'Error'
    : audioEnabled
    ? 'Click to record — audio will be sent directly to the model'
    : 'Click to start recording'

  return (
    <button
      onClick={handleClick}
      disabled={disabled || isProcessing}
      title={titleText}
      className={`p-3 rounded-xl transition-colors relative ${
        buttonState === 'recording'
          ? 'bg-theme-danger hover:bg-theme-danger-hover animate-pulse'
          : showError
          ? 'bg-theme-amber hover:bg-theme-amber'
          : isProcessing
          ? 'bg-theme-purple'
          : audioEnabled
          ? 'bg-theme-bg-elevated hover:bg-theme-bg-active ring-1 ring-theme-purple/40'
          : 'bg-theme-bg-elevated hover:bg-theme-bg-active'
      } disabled:opacity-50 disabled:cursor-not-allowed`}
    >
      {isProcessing ? (
        <Loader2 className="w-5 h-5 animate-spin text-theme-purple" />
      ) : buttonState === 'recording' ? (
        <MicOff className="w-5 h-5 text-white" />
      ) : showError ? (
        <AlertCircle className="w-5 h-5 text-white" />
      ) : (
        <Mic className={`w-5 h-5 ${audioEnabled ? 'text-theme-purple' : 'text-theme-subtle'}`} />
      )}
    </button>
  )
}
