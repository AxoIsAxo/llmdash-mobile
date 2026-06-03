import React, { useState, useRef, useEffect, useCallback } from 'react'
import { Mic, MicOff, Loader2, AlertCircle } from 'lucide-react'

interface VoiceButtonProps {
  onTranscribed: (text: string) => void
  disabled?: boolean
}

type ButtonState = 'idle' | 'recording' | 'transcribing' | 'error'

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
    if (disabled || buttonState === 'transcribing') return

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
        : 'audio/webm'

      const mediaRecorder = new MediaRecorder(stream, { mimeType })
      mediaRecorderRef.current = mediaRecorder

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      mediaRecorder.onstop = async () => {
        cleanupStream()

        if (chunksRef.current.length === 0) {
          setButtonState('idle')
          return
        }

        setButtonState('transcribing')

        try {
          const audioBlob = new Blob(chunksRef.current, { type: mimeType })
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
  }, [buttonState, disabled, onTranscribed, cleanupStream, stopRecording])

  useEffect(() => {
    return () => {
      cleanupStream()
    }
  }, [cleanupStream])

  const showError = buttonState === 'error'

  return (
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
          ? 'bg-red-600 hover:bg-red-500 animate-pulse'
          : showError
          ? 'bg-amber-600 hover:bg-amber-500'
          : buttonState === 'transcribing'
          ? 'bg-purple-600'
          : 'bg-gray-800 hover:bg-gray-700'
      } disabled:opacity-50 disabled:cursor-not-allowed`}
    >
      {buttonState === 'transcribing' ? (
        <Loader2 className="w-5 h-5 animate-spin text-purple-400" />
      ) : buttonState === 'recording' ? (
        <MicOff className="w-5 h-5 text-white" />
      ) : showError ? (
        <AlertCircle className="w-5 h-5 text-white" />
      ) : (
        <Mic className="w-5 h-5 text-gray-400" />
      )}
    </button>
  )
}
